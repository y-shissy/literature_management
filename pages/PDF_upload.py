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

from function import store_metadata_in_db, search_doi_from_filename, display_metadata,process_pdf,upload_to_google_drive,create_temp_file

# ページ設定
st.set_page_config(
    page_title="PDF Uploader",
    layout="wide",
    initial_sidebar_state="expanded",
)
#Google drive
drive=st.session_state['drive']

# メイン関数
def main():
    st.markdown("### PDFアップロード")
    DB_FILE = "literature_database.db"
    conn = sqlite3.connect(DB_FILE)

    option = st.radio("操作を選択してください", ('DOI自動判別', 'DOI手動入力'))

    if option == 'DOI自動判別':
        uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
        if uploaded_file:
            # 一時ファイルの作成
            temp_file_path, temp_file_link = create_temp_file(uploaded_file)

            if temp_file_path:
                doi, first_text = process_pdf(temp_file_path)

                if not doi:
                    search_term = os.path.splitext(uploaded_file.name)[0]
                    doi = search_doi_from_filename(search_term)
                    st.write(f"Search term: {search_term}")
                    st.write(f"Searched DOI: {doi}")

                if doi:
                    st.success(f"DOI found: {doi}")
                    metadata = display_metadata(doi)

                    # データベースへの格納処理
                    db_success = store_metadata_in_db(DB_FILE, metadata, None, uploaded_file, None)

                    if db_success:
                        # Google Driveにアップロード
                        temp_file_link = upload_to_google_drive(drive, temp_file_path, uploaded_file.name)
                        if temp_file_link:
                            st.success("ファイルがGoogle Driveにアップロードされました。")
                        else:
                            st.error("Google Driveへのアップロードに失敗しました。")
                else:
                    st.error("DOI could not be found.")
            else:
                st.error("一時ファイルの作成に失敗しました。")

    elif option == 'DOI手動入力':
        # DOIを手動入力
        doi_input = st.text_input("DOIを入力してください")
        if doi_input:
            # メタデータの表示
            metadata = display_metadata(doi_input)

            if metadata:
                st.success(f"DOI found: {doi_input}")

                # PDFアップロード
                uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
                if uploaded_file:
                    # 一時ファイルの作成
                    temp_file_path, temp_file_link = create_temp_file(uploaded_file)

                    if temp_file_path:
                        # データベースへの格納処理
                        db_success = store_metadata_in_db(DB_FILE, metadata, None, uploaded_file, None)

                        if db_success:
                            # Google Driveへのアップロード
                            file_link = upload_to_google_drive(drive, temp_file_path, uploaded_file.name)

                            if file_link:
                                st.success("ファイルがGoogle Driveにアップロードされました。")
                            else:
                                st.error("Google Driveへのアップロードに失敗しました。")
                        else:
                            st.error("データベースへの格納に失敗しました。")
                    else:
                        st.error("一時ファイルの作成に失敗しました。")
            else:
                st.error("DOIに関連するメタデータが見つかりませんでした。")





if __name__ == "__main__":
    main()