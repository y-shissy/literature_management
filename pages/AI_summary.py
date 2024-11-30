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
# 関数読込

from function import store_metadata_in_db, handle_pdf_upload,store_metadata_in_db_ai,download_file,extract_text_from_pdf,translate_and_summarize,upload_db_to_google_drive

# ページ設定
st.set_page_config(
    page_title="PDF Uploader",
    layout="wide",
    initial_sidebar_state="expanded",
)
#Google drive
drive=st.session_state['drive']

DB_FILE = "literature_database.db"

# メイン処理
def main():
    st.markdown("### アップロード済PDFのAI自動要約")

    conn = sqlite3.connect(DB_FILE)

    # 文献リスト読み込み
    edited_df = st.session_state["df"].copy()
    default_rows = edited_df[edited_df['要約'].isna() | (edited_df['要約'] == '')]['id']

    # データ選択（マルチセレクト）
    selected_rows = st.multiselect(
        "要約を行うデータを選択", 
        options=edited_df['id'], 
        default=default_rows,
        format_func=lambda x: edited_df[edited_df['id'] == x]['タイトル'].iloc[0]
    )

    # 選択した文献の情報を動的に表示
    if selected_rows:
        st.markdown("### 選択された文献の詳細情報")
        for row_id in selected_rows:
            doc_info = edited_df[edited_df['id'] == row_id]
            st.markdown(f"#### 文献 ID: {row_id}")
            st.write(f"**タイトル**: {doc_info['タイトル'].iloc[0]}")
            st.write(f"**著者**: {doc_info['著者'].iloc[0]}")
            st.write(f"**ジャーナル**: {doc_info['ジャーナル'].iloc[0]}")
            st.write(f"**巻**: {doc_info['巻'].iloc[0]}")
            st.write(f"**号**: {doc_info['号'].iloc[0]}")
            st.write(f"**年**: {doc_info['年'].iloc[0]}")
            st.write(f"**DOI URL**: {doc_info['doi_url'].iloc[0]}")

    # 表示画面用の column_config 設定
    column_config = {
        'doi_url': st.column_config.LinkColumn('Web', display_text='URL'),
        'Read': st.column_config.CheckboxColumn('Read'),
        'キーワード': st.column_config.ListColumn("キーワード", help="キーワード", width="medium")
    }
    
    # 特定カラムを表示上除外してデータを表示
    st.dataframe(
        edited_df.drop(columns=['開始ページ', '終了ページ', 'ファイルリンク']),
        column_config=column_config, 
        hide_index=True, 
        use_container_width=True
    )

    # 要約処理
    if st.button("要約"):
        progress_bar = st.progress(0)

        for i, row_id in enumerate(selected_rows):
            selected_file_path = edited_df[edited_df['id'] == row_id]["ファイルリンク"].iloc[0]
            file_id = selected_file_path.split("id=")[-1]
            pdf_file_path = download_file(drive, file_id)
            content = extract_text_from_pdf(pdf_file_path)

            # 要約とキーワード・カテゴリの取得
            summary, keyword_res, category_res = translate_and_summarize(content)
            keywords_str = ','.join(keyword_res)

            # データフレーム更新
            edited_df.loc[edited_df["id"] == row_id, "キーワード"] = keywords_str
            edited_df.loc[edited_df["id"] == row_id, "要約"] = summary
            edited_df.loc[edited_df["id"] == row_id, "カテゴリ"] = category_res

            st.success(f"要約完了: {edited_df.loc[edited_df['id'] == row_id, 'タイトル'].iloc[0]}")
            st.markdown("##### タイトル")
            st.write(edited_df.loc[edited_df["id"] == row_id, "タイトル"].iloc[0])
            st.markdown("##### カテゴリ")
            st.write(category_res)
            st.markdown("##### キーワード")
            st.write(keywords_str)
            st.markdown("##### 要約")
            st.write(summary)

            # プログレスバー更新
            progress_bar.progress((i + 1) / len(selected_rows))

        # データベース更新処理
        update_database(conn, edited_df)

        st.success("変更が保存されました")
        upload_db_to_google_drive(DB_FILE, drive)

def update_database(conn, edited_df):
    """データベース更新処理。"""
    edited_df.to_sql("temp_metadata", conn, if_exists="replace", index=False)
    existing_ids = pd.read_sql("SELECT id FROM metadata", conn)["id"]
    new_ids = edited_df["id"]
    ids_to_delete = existing_ids[~existing_ids.isin(new_ids)]

    with conn:
        try:
            if not ids_to_delete.empty:
                conn.execute(f"DELETE FROM metadata WHERE id IN ({','.join(map(str, ids_to_delete))})")
            conn.execute("DELETE FROM metadata WHERE id IN (SELECT id FROM temp_metadata)")
            conn.execute("""
                INSERT INTO metadata (タイトル,著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,カテゴリ,doi,doi_url,ファイルリンク,メモ,Read)
                SELECT タイトル,著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,カテゴリ,doi,doi_url,ファイルリンク,メモ,Read FROM temp_metadata
            """)
            conn.execute("DROP TABLE temp_metadata")
        except sqlite3.OperationalError as err:
            st.error(f"オペレーショナルエラー: {err}")



if __name__ == "__main__":
    # アプリケーションを実行
    main()
