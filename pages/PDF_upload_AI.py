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

from function import store_metadata_in_db, handle_pdf_upload,store_metadata_in_db_ai,create_temp_file

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

    option = st.radio("操作を選択してください", ('DOI自動判別+要約','DOI自動判別', 'DOI手動入力+要約','文献情報手動入力+要約'))

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
                    # ページを再実行
                    st.experimental_rerun()
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

                # ページを再実行
                st.experimental_rerun()

    elif option == 'DOI手動入力+要約':
        doi_input = st.text_input("DOIを入力してください")
        if doi_input:
            uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
            if uploaded_file:
                # PDFを処理してメタデータを取得
                metadata, file_path = handle_pdf_upload(uploaded_file, auto_doi=False, manual_doi=doi_input)
                if metadata and file_path:
                    # データベース格納関数を呼び出し
                    store_metadata_in_db_ai(DB_FILE, metadata, file_path, uploaded_file, drive)

                    # ページを再実行
                    st.experimental_rerun()


    elif option == '文献情報手動入力+要約':
        uploaded_file = st.file_uploader("PDFをアップロード", type=["pdf"])
        if uploaded_file:
            st.markdown("### 文献情報の手動入力")
            with st.form(key='manual_metadata_form'):
                title = st.text_input("タイトル")
                authors = st.text_input("著者")
                journal = st.text_input("ジャーナル")
                volume = st.text_input("巻")
                number = st.text_input("号")
                start_page = st.text_input("開始ページ")
                end_page = st.text_input("終了ページ")
                year = st.text_input("年")
                doi_input = st.text_input("DOIを入力してください")
                memo = st.text_area("メモ")
                read = st.checkbox("既読の場合チェック", value=False)
                submit_button = st.form_submit_button(label='保存')

                if submit_button:
                    # PDFファイルを一時的に保存し、ファイルパスを取得
                    temp_file_path, _ = create_temp_file(uploaded_file)  # create_temp_fileは一時ファイルを作成する関数

                    if not temp_file_path:
                        st.error("一時ファイルの作成に失敗しました。")
                        return

                    # 入力内容からメタデータを作成
                    metadata = {
                        'doi': doi_input,
                        'タイトル': title,
                        '著者': authors,
                        'ジャーナル': journal,
                        '巻': volume,
                        '号': number,
                        '開始ページ': start_page,
                        '終了ページ': end_page,
                        '年': year,
                    }

                    # データベース格納関数を呼び出し
                    store_metadata_in_db_ai(DB_FILE, metadata, temp_file_path, uploaded_file, drive)

                    # ページを再実行
                    st.experimental_rerun()

if __name__ == "__main__":
    main()