import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import sqlite3
import os
import tempfile
import shutil

from streamlit_pdf_viewer import pdf_viewer
import pandas as pd
import re
import fitz
import time
import requests
from langdetect import detect
from bs4 import BeautifulSoup
from sqlalchemy import Column, Integer, String, Boolean, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from database import get_session, Metadata

from openai import OpenAI
from llama_index.core import download_loader, VectorStoreIndex, Settings, SimpleDirectoryReader
import tiktoken
import urllib.parse

import pytesseract
from pdf2image import convert_from_path

# ページ設定
st.set_page_config(layout="wide")

DB_FILE = "literature_database.db"

# 初期設定
# 文献にタグ付けするカテゴリの選択肢
categories = ["A","B"]
# 文献にタグ付けするキーワードの選択肢
keywords = ["a","b"]

#session_stateに保存
st.session_state["categories"]=categories
st.session_state["keywords"]=keywords


# Google Drive 認証設定
def google_drive_auth(creds_file_path):
    gauth = GoogleAuth()
    gauth.LoadCredentialsFile(creds_file_path)
    if gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    return gauth, GoogleDrive(gauth)

# SQLiteデータベースを初期化
def initialize_db():
    Base = declarative_base()
    class Metadata(Base):
        __tablename__ = 'metadata'
        id = Column(Integer, primary_key=True, autoincrement=True)
        タイトル = Column(String)
        著者 = Column(String)
        ジャーナル = Column(String)
        巻 = Column(String)
        号 = Column(String)
        開始ページ = Column(String)
        終了ページ = Column(String)
        年 = Column(Integer)
        要約 = Column(String)
        キーワード = Column(String)
        カテゴリ = Column(String)
        doi = Column(String, nullable=False)
        doi_url = Column(String)
        ファイルリンク = Column(String)
        メモ = Column(String)
        Read = Column(Boolean, default=False)

    DATABASE_URL=f"sqlite:///{DB_FILE}"
    engine=create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    SessionLocal=sessionmaker(bind=engine)

# SQLiteデータベースを読み込む
def read_db():
    conn = sqlite3.connect(DB_FILE)
    DATABASE_URL=f"sqlite:///{DB_FILE}"
    engine=create_engine(DATABASE_URL)
    SessionLocal=sessionmaker(bind=engine)
    if 'df' not in st.session_state:
        try:
            df = pd.read_sql("SELECT * FROM metadata", conn)
            st.session_state["df"] = df
        except Exception as e:
            st.error(f"データの読み込み中にエラーが発生しました：{e}")

# Google DriveからSQLiteデータベースをダウンロード
def download_db_from_google_drive(drive):
    file_list = drive.ListFile({'q': f"title='{DB_FILE}' and trashed=false"}).GetList()
    if file_list:
        gfile = file_list[0]  # 最初のファイルを取得
        gfile.GetContentFile(DB_FILE)  # ローカルにデータベースを保存
        st.success(f"{DB_FILE} をGoogle Driveからダウンロードしました。")
    else:
        st.error(f"{DB_FILE} がGoogle Drive内に見つかりません。新規作成します。")
        initialize_db()

# メイン処理
def main():
    st.title(":book:文献管理アプリ")

    # 認証情報ファイルのアップロード
    if "drive" not in st.session_state:
        uploaded_creds_file = st.file_uploader("認証情報ファイル (`mycreds.txt`) をアップロード", type=["txt"])

        if uploaded_creds_file:
            # 一時ファイルとして`mycreds.txt`を保存
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_creds_file:
                temp_creds_file.write(uploaded_creds_file.read())
                temp_creds_path = temp_creds_file.name

            # Google Drive 認証
            try:
                gauth, drive = google_drive_auth(temp_creds_path)
                st.session_state['drive'] = drive  # 認証したDriveオブジェクトをsession_stateに保存
                st.success("Google Drive認証に成功しました。")
            except Exception as e:
                st.error(f"Google Drive認証に失敗しました: {e}")
                st.stop()
        else:
            st.warning("Google Driveの認証には`mycreds.txt`ファイルをアップロードしてください。")
            st.stop()
    else:
        st.success("すでにGoogle Driveに認証されています。")


    # タブ別表示
    items=["データベース表示","文献追加","ナレッジ検索","設定"]
    tabs=st.tabs(items)
    with tabs[0]:

        # Google Driveからデータベースをダウンロード，データが無い場合は初期化
        download_db_from_google_drive(drive)
        # データベース読み込み
        read_db()
        
        # カテゴリカラムのユニークな値を抽出
        unique_category = st.session_state["df"]["カテゴリ"].unique()
        # journalカラムのユニークな値を抽出
        unique_journals = st.session_state["df"]["ジャーナル"].unique()
        # authorsカラムのユニークな名前を抽出（カンマ区切りで抽出）
        authors = st.session_state["df"]["著者"].str.split(',').explode().str.strip().unique()
        # keywordsカラムのユニークなキーワードを抽出（カンマ区切りで抽出）
        keywords = st.session_state["df"]["キーワード"].str.split(',').explode().str.strip().unique()

        st.markdown('#### :mag:フィルタリング項目')
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            selected_status = st.selectbox("既読・未読", options=[None, True, False], format_func=lambda x: "すべて" if x is None else ("既読" if x else "未読"))
        with col2:
            selected_category = st.selectbox("カテゴリ", options=[None] + list(unique_category), format_func=lambda x: "すべて" if x is None else x)
        with col3:
            selected_keyword = st.selectbox("キーワード", options=[None] + list(keywords), format_func=lambda x: "すべて" if x is None else x)
        with col4:
            selected_journal = st.selectbox("ジャーナル", options=[None] + list(unique_journals), format_func=lambda x: "すべて" if x is None else x)
        with col5:
            selected_author = st.selectbox("著者", options=[None] + list(authors), format_func=lambda x: "すべて" if x is None else x)
        with col6:
            text_for_filter = st.text_input("検索語句")

        # フィルタリング条件に基づいてデータをフィルタリング
        filtered_df = st.session_state["df"].copy()

        if selected_status is not None:
            filtered_df = filtered_df[filtered_df["Read"] == selected_status]
        if selected_category is not None:
            filtered_df = filtered_df[filtered_df["カテゴリ"]  == selected_category]
        if selected_journal is not None:
            filtered_df = filtered_df[filtered_df["ジャーナル"] == selected_journal]
        if selected_author is not None:
            filtered_df = filtered_df[filtered_df["著者"].str.contains(selected_author)]
        if selected_keyword is not None:
            filtered_df = filtered_df[filtered_df["キーワード"].str.contains(selected_keyword)]
        if text_for_filter:
            filtered_df = filtered_df[filtered_df.apply(lambda row: row.astype(str).str.contains(text_for_filter, case=False).any(), axis=1)]

        # DataFrameを表示
        st.markdown('#### :open_book:文献リスト表示')
        # 表示画面用のcolumn_config設定
        column_config={'doi_url': st.column_config.LinkColumn('Web', display_text='URL'),
        'Read': st.column_config.CheckboxColumn('Read'),
        'キーワード': st.column_config.ListColumn(
            "キーワード",
            help="キーワード",
            width="medium",
        )}
        #特定カラムを表示上除外して，データを表示
        st.dataframe(filtered_df.drop(columns=['開始ページ', '終了ページ','ファイルリンク']), column_config=column_config, hide_index=True, use_container_width=True)

        st.markdown('#### :pencil:データ編集')
        # データ編集のチェックボックス
        edit_data = st.checkbox("データを編集する")
        if edit_data:
            # 編集画面用のcolumn_config設定
            column_config_edit={'doi_url': st.column_config.LinkColumn('Web', display_text='URL'),
            'Read': st.column_config.CheckboxColumn('Read'),
            "カテゴリ": st.column_config.SelectboxColumn(
                "カテゴリ",
                help="カテゴリ",
                width="medium",
                options=categories,
                required=True,
            )}
            # ユーザーが行を追加・削除できるようにする
            edited_df = st.data_editor(filtered_df, num_rows="dynamic", column_config=column_config_edit)

            # 変更を保存
            if st.button("変更を保存"):
                # 一時的なテーブルを作成（'id'列を除く）
                edited_df.to_sql("temp_metadata", conn, if_exists="replace", index=False)
                # 元データベースから削除されるべき行を特定する
                existing_ids = pd.read_sql("SELECT id FROM metadata", conn)['id']
                new_ids = edited_df['id']
                ids_to_delete = existing_ids[~existing_ids.isin(new_ids)]
                # 元のテーブルに全カラムを挿入
                with conn:
                    # 対応するidを持つ行を削除
                    if not ids_to_delete.empty:
                        conn.execute(f"DELETE FROM metadata WHERE id IN ({','.join(map(str, ids_to_delete))})")
                    # 編集したデータフレームの内容を元のデータフレームに追加
                    conn.execute("DELETE FROM metadata WHERE id IN (SELECT id FROM temp_metadata)")
                    conn.execute("""
                        INSERT INTO metadata (タイトル, 著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,カテゴリ,doi,doi_url,ファイルリンク,メモ,Read)
                        SELECT タイトル, 著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,カテゴリ,doi,doi_url,ファイルリンク,メモ,Read FROM temp_metadata
                    """)
                    conn.execute("DROP TABLE temp_metadata")

                # データを再読み込みし、セッション状態を更新
                df = pd.read_sql("SELECT * FROM metadata", conn)
                st.session_state["df"] = df
                st.success("変更が保存されました．間もなくリロードします．")
                time.sleep(3)
                st.experimental_rerun()

        st.markdown('#### :open_file_folder:ファイル表示')
        # ファイル表示のチェックボックス
        file_view = st.checkbox("PDFファイルを表示する")
        if file_view:
            # id-タイトルの形式で選択肢を作成
            options = filtered_df.apply(lambda row: f"{row['id']}-{row['タイトル']}", axis=1)

            # レコード選択のためのセレクトボックス（初期選択なし）
            selected_option = st.selectbox("PDFファイル選択", options,index=0)

            # 選択されたレコードのファイルリンクを取得
            if selected_option:
                selected_index = options[options == selected_option].index[0]
                selected_file_path = filtered_df.loc[selected_index, 'ファイルリンク']

                # PDFの表示
                if selected_file_path:
                    with open(selected_file_path, "rb") as pdf_file:
                        binary_data = pdf_file.read()
                        pdf_viewer(input=binary_data, width=1400)

    with tabs[1]:
        st.markdown("### PDFアップロード")
        if st.button("PDF Uploader"):
            st.switch_page("pages/PDF_upload.py")

        st.markdown("### PDFアップロード・AI自動要約")
        if st.button("PDF Uploader with AI"):
            st.switch_page("pages/PDF_upload_AI.py")

        st.markdown("### アップロード済PDFのAI自動要約")
        if st.button("AI Summary"):
            st.switch_page("pages/AI_summary.py")


        # # アップロードされたPDFを処理
        # uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
        # if uploaded_file:
        #     # Google Drive にPDFをアップロード
        #     file_link = upload_to_google_drive(drive, uploaded_file)

        #     if file_link:  # アップロードに成功した場合
        #         # SQLiteデータベースに記録
        #         conn = sqlite3.connect(DB_FILE)
        #         c = conn.cursor()
        #         try:
        #             c.execute("INSERT INTO pdf_data (title, link) VALUES (?, ?)", (uploaded_file.name, file_link))
        #             conn.commit()
        #             st.success("PDFの情報がデータベースに追加されました！")
        #         except Exception as e:
        #             st.error(f"データベースへの追加に失敗しました: {e}")
        #         finally:
        #             conn.close()

        #         # データベースをGoogle Driveにアップロード
        #         db_link = upload_db_to_google_drive(drive)
        #         if db_link:  # データベースアップロードに成功した場合
        #             st.success("データベースを更新しました！")
        #             st.write(f"PDFリンク: [ここをクリック]({file_link})")
        #             st.write(f"データベースはGoogle Driveにアップロードされました。リンク: [ここをクリック]({db_link})")

        # # データベースから保存済みPDFを表示
        # try:
        #     conn = sqlite3.connect(DB_FILE)
        #     c = conn.cursor()
        #     c.execute("SELECT title, link FROM pdf_data")
        #     rows = c.fetchall()
        #     st.write("保存済みPDF一覧:")
        #     for row in rows:
        #         st.write(f"- {row[0]}: [リンク]({row[1]})")
        #     conn.close()
        # except Exception as e:
        #     st.error(f"データベースの取得に失敗しました: {e}")
            
    with tabs[2]:
        st.write("under construction")
    with tabs[3]:
        st.write("under construction")

if __name__ == "__main__":
    # アプリケーションを実行
    main()

css ='''
<style>
    .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
    font-size:1.5rem;
    }
</style>
'''

st.markdown(css,unsafe_allow_html=True)