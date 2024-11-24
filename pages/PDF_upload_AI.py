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

from function import store_metadata_in_db, handle_pdf_upload,store_metadata_in_db_ai

# ページ設定
st.set_page_config(
    page_title="PDF Uploader",
    layout="wide",
    initial_sidebar_state="expanded",
)
#Google drive
drive=st.session_state['drive']

def main():
    st.markdown("### PDFアップロード・AI自動要約")
    DB_FILE = "literature_database.db"

    option = st.radio("操作を選択してください", ('DOI自動判別+要約','DOI自動判別', 'DOI手動入力'))

    if option == 'DOI自動判別+要約':
        uploaded_files = st.file_uploader("PDFをアップロード (複数選択可能)", type=["pdf"], accept_multiple_files=True)
        if uploaded_files:
            for uploaded_file in uploaded_files:
                st.markdown(f"### 処理中: {uploaded_file.name}")
                # 各PDFを処理してDOIとメタデータを取得
                metadata, file_path = handle_pdf_upload(uploaded_file, auto_doi=True)
                if metadata and file_path:
                    # データベース格納関数を呼び出し
                    store_metadata_in_db_ai(DB_FILE, metadata, file_path, uploaded_file, drive)
                else:
                    st.warning(f"{uploaded_file.name} の処理に失敗しました。")

    elif option == 'DOI自動判別':
        uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
        if uploaded_file:
            # PDFを処理してDOIとメタデータを取得
            metadata, file_path = handle_pdf_upload(uploaded_file, auto_doi=True)
            if metadata and file_path:
                # データベース格納関数を呼び出し
                store_metadata_in_db(DB_FILE, metadata, file_path, uploaded_file, drive)

    elif option == 'DOI手動入力':
        doi_input = st.text_input("DOIを入力してください")
        if doi_input:
            uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
            if uploaded_file:
                # PDFを処理してメタデータを取得
                metadata, file_path = handle_pdf_upload(uploaded_file, auto_doi=False, manual_doi=doi_input)
                if metadata and file_path:
                    # データベース格納関数を呼び出し
                    store_metadata_in_db(DB_FILE, metadata, file_path, uploaded_file, drive)




if __name__ == "__main__":
    main()