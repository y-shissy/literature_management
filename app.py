import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import sqlite3
import os
import tempfile
import shutil
import base64

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
# 関数読込
from function import download_file,upload_db_to_google_drive

# ページ設定
st.set_page_config(layout="wide")

# ファイル名設定
# データベースファイル
DB_FILE = "literature_database.db"
# キーワード，カテゴリ格納ファイル
keywords_categories_file= 'keywords_categories.csv'

# 初期化処理
def initialize_app():
    if "initialized" in st.session_state:
        return
    # Google Drive 認証
    if "drive" not in st.session_state:
        uploaded_creds_file = st.file_uploader("認証情報ファイル (`mycreds.txt`) をアップロード", type=["txt"])
        if uploaded_creds_file:
            # 一時ファイルを作成して認証情報を読み込む
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_creds_file:
                temp_creds_file.write(uploaded_creds_file.read())
                temp_creds_path = temp_creds_file.name

            gauth = GoogleAuth()
            gauth.LoadCredentialsFile(temp_creds_path)

            # トークンのチェック
            if gauth.access_token_expired:
                if hasattr(gauth, 'refresh_token'):
                    gauth.Refresh()  # リフレッシュトークンが存在する場合にリフレッシュ
                else:
                    st.warning("リフレッシュトークンが存在しません。再認証が必要です。")
                    st.stop()
            elif not gauth.access_token:  # アクセストークンがない場合
                st.warning("アクセストークンが存在しません。再認証が必要です。")
                st.stop()
            else:
                gauth.Authorize()  # 初回認証またはトークンが有効な場合の処理

            # 認証情報をセッションステートに保存
            st.session_state["credentials"] = {
                "access_token": gauth.access_token,
                "refresh_token": gauth.refresh_token,
                "client_id": gauth.client_id,
                "client_secret": gauth.client_secret,
                "expiration_timestamp": gauth.authorization_expires_at.strftime('%Y-%m-%d %H:%M:%S')
            }

            # Driveオブジェクトをセッションに保存
            st.session_state["drive"] = GoogleDrive(gauth)

        else:
            st.warning("Google Drive認証ファイルをアップロードしてください。")
            st.stop()
    else:
        # セッションにすでにDriveが存在する場合、認証情報を再利用
        gauth = GoogleAuth()

        credentials = st.session_state.get("credentials")

        if credentials:
            gauth.credentials = {
                "access_token": credentials["access_token"],
                "refresh_token": credentials["refresh_token"],
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"]
            }
            gauth.Authorize()
            st.session_state["drive"] = GoogleDrive(gauth)
        else:
            st.warning("認証情報が見つかりません。再認証が必要です。")
            st.stop()
            
    # データベース確認と読み込み
    if not os.path.exists(DB_FILE):
        db_temp_path = download_db_from_drive(st.session_state["drive"], DB_FILE)
        if db_temp_path:
            shutil.copy(db_temp_path, DB_FILE)  # ダウンロードしたファイルをアプリケーションの作業ディレクトリにコピー
        else:
            st.warning(f"{DB_FILE} がGoogle Driveに見つかりません。新しいデータベースを作成します。")
            initialize_db()

    try:
        conn = sqlite3.connect(DB_FILE)
        df = pd.read_sql("SELECT * FROM metadata", conn)
    except sqlite3.OperationalError:
        st.warning("データベースが空です。新しいデータベースを作成します。")
        initialize_db()
        df = pd.DataFrame()

    st.session_state["df"] = df


    # Google Driveからキーワードとカテゴリを読み込み
    keywords_all = load_keywords_from_drive(st.session_state["drive"])
    categories_all = load_categories_from_drive(st.session_state["drive"])

    # ファイルが存在しない場合、新しいファイルを作成
    if not keywords_all:
        st.warning("キーワードが見つかりません。新しいファイルを作成します。")
        save_keywords_to_drive(st.session_state["drive"], [])
    if not categories_all:
        st.warning("カテゴリが見つかりません。新しいファイルを作成します。")
        save_categories_to_drive(st.session_state["drive"], [])

    # キーワード・カテゴリをセッション状態に保存
    st.session_state["keywords_all"] = keywords_all
    st.session_state["categories_all"] = categories_all

    # 初期化フラグを設定
    st.session_state["initialized"] = True

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

def download_db_from_drive(drive, db_file_name):
    exists, file_id = file_exists_in_drive(drive, db_file_name)
    if not exists:
        st.error(f"{db_file_name} がGoogle Driveに見つかりません。")
        return None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as temp_file:
            file = drive.CreateFile({'id': file_id})
            file.GetContentFile(temp_file.name)
            return temp_file.name  # ダウンロードしたファイルのパスを返す
    except Exception as e:
        st.error(f"Google Driveからデータベースをダウンロード中にエラーが発生しました: {e}")
        return None

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


# キーワードをGoogle Driveに保存する関数
def save_keywords_to_drive(drive, keywords):
    # 既存ファイルのチェック
    exists, file_id = file_exists_in_drive(drive, 'keywords.csv')

    # 既存ファイルがあれば削除
    if exists:
        try:
            existing_file = drive.CreateFile({'id': file_id})
            existing_file.Trash()  # ファイルをゴミ箱に移動
        except Exception as e:
            st.warning(f"既存のファイルを削除中にエラーが発生しました: {e}")

    # 新しいファイルを作成
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temp_file:
        df = pd.DataFrame({'キーワード': keywords})
        df.to_csv(temp_file.name, index=False)

        # ファイルをGoogle Driveにアップロード
        new_file_drive = drive.CreateFile({'title': 'keywords.csv'})
        new_file_drive.SetContentFile(temp_file.name)
        new_file_drive.Upload()

    st.success("キーワードがGoogle Driveに保存されました。")

# カテゴリをGoogle Driveに保存する関数
def save_categories_to_drive(drive, categories):
    # 既存ファイルのチェック
    exists, file_id = file_exists_in_drive(drive, 'categories.csv')

    # 既存ファイルがあれば削除
    if exists:
        try:
            existing_file = drive.CreateFile({'id': file_id})
            existing_file.Trash()  # ファイルをゴミ箱に移動
        except Exception as e:
            st.warning(f"既存のファイルを削除中にエラーが発生しました: {e}")

    # 新しいファイルを作成
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temp_file:
        df = pd.DataFrame({'カテゴリ': categories})
        df.to_csv(temp_file.name, index=False)

        # ファイルをGoogle Driveにアップロード
        new_file_drive = drive.CreateFile({'title': 'categories.csv'})
        new_file_drive.SetContentFile(temp_file.name)
        new_file_drive.Upload()

    st.success("カテゴリがGoogle Driveに保存されました。")

# Google Driveに指定のファイルが存在するか確認する関数
def file_exists_in_drive(drive, filename):
    file_list = drive.ListFile({'q': f"title='{filename}' and trashed=false"}).GetList()
    return len(file_list) > 0, file_list[0].get('id') if file_list else None

# Google Driveからキーワードを読み込む関数
def load_keywords_from_drive(drive):
    exists, file_id = file_exists_in_drive(drive, 'keywords.csv')
    if exists:
        try:
            file = drive.CreateFile({'id': file_id})
            file.GetContentFile('temp_keywords.csv')
            df = pd.read_csv('temp_keywords.csv')
            return df['キーワード'].dropna().tolist()
        except Exception as e:
            st.warning(f"キーワードの読み込み中にエラーが発生しました: {e}")
            return []
    else:
        return []  # ファイルが存在しない場合は空のリストを返す

# Google Driveからカテゴリを読み込む関数
def load_categories_from_drive(drive):
    exists, file_id = file_exists_in_drive(drive, 'categories.csv')
    if exists:
        try:
            file = drive.CreateFile({'id': file_id})
            file.GetContentFile('temp_categories.csv')
            df = pd.read_csv('temp_categories.csv')
            return df['カテゴリ'].dropna().tolist()
        except Exception as e:
            st.warning(f"カテゴリの読み込み中にエラーが発生しました: {e}")
            return []
    else:
        return []  # ファイルが存在しない場合は空のリストを返す

    
# メイン処理
def main():
    st.title(":book:文献管理アプリ")

    # 初期化
    initialize_app()

    # タブ別表示
    items=["データベース表示","文献追加","ナレッジ検索","設定"]
    tabs=st.tabs(items)
    with tabs[0]:

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
                options=st.session_state["categories_all"],
                required=True,
            )}
            # ユーザーが行を追加・削除できるようにする
            edited_df = st.data_editor(filtered_df, num_rows="dynamic", column_config=column_config_edit)

            # 変更を保存
            if st.button("変更を保存"):
                conn = sqlite3.connect(DB_FILE)
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
                # Google Driveにデータベースをアップロード
                upload_db_to_google_drive(DB_FILE, st.session_state['drive'])

                time.sleep(3)
                st.experimental_rerun()

        st.markdown('#### :open_file_folder:ファイル表示')

        file_view = st.checkbox("PDFファイルを表示する")

        if file_view:
            # id-タイトルの形式で選択肢を作成
            options = filtered_df.apply(lambda row: f"{row['id']}-{row['タイトル']}", axis=1)

            # レコード選択のためのセレクトボックス（初期選択なし）
            selected_option = st.selectbox("PDFファイル選択", options, index=None)  # 初期選択なし

            # 選択されたレコードのファイルリンクを取得
            if selected_option:
                selected_index = options[options == selected_option].index[0]
                selected_file_path = filtered_df.loc[selected_index, 'ファイルリンク']

                # Google DriveからファイルIDを抽出
                if selected_file_path:
                    file_id = selected_file_path.split("id=")[-1]

                    # PDFファイルをダウンロード
                    pdf_file_path = download_file(st.session_state['drive'], file_id)

                    # PDFを表示
                    pdf_viewer(pdf_file_path)  # streamlit_pdf_viewerでPDFを表示

                    # 論文情報の表示をサイドバーに追加
                    st.sidebar.markdown("### 論文情報")
                    title = filtered_df.loc[selected_index, 'タイトル']
                    authors = filtered_df.loc[selected_index, '著者']
                    journal = filtered_df.loc[selected_index, 'ジャーナル']
                    year = filtered_df.loc[selected_index, '年']
                    category = filtered_df.loc[selected_index, 'カテゴリ']
                    keywords = filtered_df.loc[selected_index, 'キーワード']
                    abstract = filtered_df.loc[selected_index, '要約']
                    notes = filtered_df.loc[selected_index, 'メモ']

                    st.sidebar.markdown(f"**タイトル**: {title}")
                    st.sidebar.markdown(f"**著者**: {authors}")
                    st.sidebar.markdown(f"**ジャーナル**: {journal}")
                    st.sidebar.markdown(f"**年**: {year}")
                    st.sidebar.markdown(f"**カテゴリ**: {category}")
                    st.sidebar.markdown(f"**キーワード**: {keywords}")
                    st.sidebar.markdown(f"**要約**: {abstract}")
                    st.sidebar.markdown(f"**メモ**: {notes}")

                    # PDFをダウンロードするボタンをサイドバーに追加
                    download_file_name = f"{title}.pdf"  # タイトルに.pdfを追加
                    with open(pdf_file_path, "rb") as f:
                        pdf_data = f.read()
                        st.sidebar.download_button("PDFをダウンロード", pdf_data, download_file_name, mime='application/pdf')
                        
    with tabs[1]:
        st.markdown("### PDFアップロード・AI自動要約")
        if st.button("PDF Uploader with AI"):
            st.switch_page("pages/PDF_upload_AI.py")

        st.markdown("### アップロード済PDFのAI自動要約")
        if st.button("AI Summary"):
            st.switch_page("pages/AI_summary.py")


    with tabs[2]:
        st.markdown("### AIチャットボット")
        if st.button("AI Chat"):
            st.switch_page("pages/AI_chat.py")

        st.markdown("### 文献データのベクトル化")
        if st.button("RAG Setting"):
            st.switch_page("pages/RAG_setting.py")

    with tabs[3]:
        st.markdown("### カテゴリ/キーワード設定")

        # 現在のキーワードとカテゴリを横に並べて表示
        col1, col2 = st.columns(2)  # 2つの列を作成

        with col1:
            st.markdown("##### カテゴリ一覧")
            for category in st.session_state["categories_all"]:
                st.write(category)

        with col2:
            st.markdown("##### キーワード一覧")
            for keyword in st.session_state["keywords_all"] :
                st.write(keyword)

        # テキストエリアで追加の入力を受け付け
        categories_input = st.text_area("追加するカテゴリ(カンマ区切り)", placeholder="新しいカテゴリを入力", key="categories_input")
        keywords_input = st.text_area("追加するキーワード(カンマ区切り)", placeholder="新しいキーワードを入力", key="keywords_input")

        if st.button("保存"):
            # 入力をリストに変換
            new_categories = [cat.strip() for cat in categories_input.split(',') if cat.strip()]
            new_keywords = [kw.strip() for kw in keywords_input.split(',') if kw.strip()]

            if new_categories:
                # 既存のカテゴリに新しいカテゴリを追加
                save_categories_to_drive(st.session_state['drive'], st.session_state["categories_all"] + new_categories)
            if new_keywords:
                # 既存のキーワードに新しいキーワードを追加
                save_keywords_to_drive(st.session_state['drive'], st.session_state["keywords_all"]  + new_keywords)

            # 更新後のリストを表示
            st.success("新しいカテゴリとキーワード保存されました。")

            st.markdown("### 更新されたカテゴリ一覧")
            for category in st.session_state["categories_all"] + new_categories:
                st.write(category)

            st.markdown("### 更新されたキーワード一覧")
            for keyword in st.session_state["keywords_all"]  + new_keywords:
                st.write(keyword)

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