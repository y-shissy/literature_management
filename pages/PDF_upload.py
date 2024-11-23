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

from function import store_metadata_in_db, search_doi_from_filename, display_metadata,process_pdf,upload_to_google_drive

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
    engine = create_engine(f"sqlite:///{DB_FILE}")

    option = st.radio("操作を選択してください", ('DOI自動判別', 'DOI手動入力'))

    def process_doi_input(doi_input, uploaded_file):
        """
        DOI 処理共通ロジックを関数化
        """
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            # データベースに DOI が存在するか確認
            existing_record = session.query(Metadata).filter_by(doi=doi_input).first()
            if existing_record:
                st.warning("This DOI is already in the database.")
                return

            # メタデータ取得
            metadata = display_metadata(doi_input)
            if not metadata:
                st.error("Metadata could not be retrieved.")
                return

            # Google Drive に PDF をアップロード
            temp_file_path, file_link = upload_to_google_drive(drive, uploaded_file)
            if not file_link:
                st.error("File upload failed.")
                return

            # メタデータをデータベースに格納
            store_metadata_in_db(DB_FILE, metadata, file_link, uploaded_file, drive)
        finally:
            session.close()

    if option == 'DOI自動判別':
        uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
        if uploaded_file:
            # DOI を抽出
            temp_file_path, file_link = upload_to_google_drive(drive, uploaded_file)
            doi, first_text = process_pdf(temp_file_path)
            if not doi:
                # 抽出失敗時にファイル名から検索
                search_term = os.path.splitext(uploaded_file.name)[0]
                doi = search_doi_from_filename(search_term)
                st.write(f"Search term: {search_term}")
                st.write(f"Searched DOI: {doi}")

            if doi:
                st.success(f"DOI found: {doi}")
                process_doi_input(doi, uploaded_file)
            else:
                st.error("DOI could not be found.")

    elif option == 'DOI手動入力':
        doi_input = st.text_input("DOIを入力してください")
        uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
        if doi_input and uploaded_file:
            st.success(f"DOI entered: {doi_input}")
            process_doi_input(doi_input, uploaded_file)


if __name__ == "__main__":
    main()