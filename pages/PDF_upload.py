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
    conn = sqlite3.connect(DB_FILE)

    option = st.radio("操作を選択してください", ('DOI自動判別','DOI手動入力'))
    if option == 'DOI自動判別':
        # アップロードされたPDFを処理
        uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
        if uploaded_file:
            # Google Drive にPDFをアップロード
            temp_file_path, file_link = upload_to_google_drive(drive, uploaded_file)

            if temp_file_path:  # アップロードに成功した場合
                # DOI抽出処理
                doi, first_text = process_pdf(temp_file_path)  # 一時ファイルパスを渡す

                if not doi:
                    # If DOI extraction fails, search using filename
                    search_term = os.path.splitext(uploaded_file.name)[0]
                    doi = search_doi_from_filename(search_term)
                    st.write(f"Search term: {search_term}") 
                    st.write(f"Searched DOI: {doi}") 

                if doi:
                    st.success(f"DOI found: {doi}")
                    #メタデータ表示
                    metadata=display_metadata(doi)
                    #データベースへの格納処理
                    store_metadata_in_db(DB_FILE,metadata,file_link,uploaded_file,drive)
                    #再読み込み
                    st.cache_data.clear()  # キャッシュをクリア
                    df = pd.read_sql("SELECT * FROM metadata", conn)  # データ再読み込み
                    st.session_state["df"] = df

                else:
                    st.error("DOI could not be found.")
                    return  # 最後に処理を終了させる


    elif option == 'DOI手動入力':
        doi_input = st.text_input("DOIを入力してください")
        if doi_input:
            metadata=display_metadata(doi_input)

            if metadata:
                st.success(f"DOI found: {doi_input}")


                # アップロードされたPDFを処理
                uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
                if uploaded_file:
                    # Google Drive にPDFをアップロード
                    temp_file_path, file_link = upload_to_google_drive(drive, uploaded_file)

                    if file_link:  # アップロードに成功した場合

                        #データベースへの格納処理
                        store_metadata_in_db(DB_FILE,metadata,file_link,uploaded_file,drive)
                        #再読み込み
                        st.cache_data.clear()  # キャッシュをクリア
                        df = pd.read_sql("SELECT * FROM metadata", conn)  # データ再読み込み
                        st.session_state["df"] = df

                    else:
                        st.error("DOI could not be found.")
                        return  # 最後に処理を終了させる



if __name__ == "__main__":
    main()