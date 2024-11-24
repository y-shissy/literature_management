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

from function import store_metadata_in_db, handle_pdf_upload,store_metadata_in_db_ai,download_file,extract_text_from_pdf,translate_and_summarize

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
    edited_df= st.session_state["df"].copy()
    # 要約対象データのデフォルト（要約カラムにデータが無いもの）
    default_rows=edited_df[edited_df['要約'].isna()|(edited_df['要約']=='')]['id']
    # データ選択
    selected_rows=st.multiselect("要約を行うデータを選択",edited_df['id'],default=default_rows)
    # 表示画面用のcolumn_config設定
    column_config={'doi_url': st.column_config.LinkColumn('Web', display_text='URL'),
    'Read': st.column_config.CheckboxColumn('Read'),
    'キーワード': st.column_config.ListColumn(
        "キーワード",
        help="キーワード",
        width="medium",
    )}
    #特定カラムを表示上除外して，データを表示
    st.dataframe(edited_df.drop(columns=['開始ページ', '終了ページ','ファイルリンク']), column_config=column_config, hide_index=True, use_container_width=True)

    # 要約処理
    if st.button("要約"):
        # プログレスバー初期化
        progress_bar=st.progress(0)

        for i,row_id in enumerate(selected_rows):
            selected_file_path = edited_df[edited_df['id']==row_id]["ファイルリンク"].iloc[0]
            file_id = selected_file_path.split("id=")[-1]
            pdf_file_path = download_file(drive, file_id)
            #PDFファイルからすべてのテキストを抽出
            content=extract_text_from_pdf(pdf_file_path)
            # 抽出したテキストから，要約とキーワードとカテゴリを取得
            summary,keyword_res,category_res=translate_and_summarize(content)
            # キーワードを文字列に変換
            keywords_str=','.join(keyword_res)

            #データフレーム更新
            edited_df.loc[edited_df["id"]==row_id, "キーワード"]=keywords_str
            edited_df.loc[edited_df["id"]==row_id, "要約"]=summary
            edited_df.loc[edited_df["id"]==row_id, "カテゴリ"]=category_res

            st.success("要約完了")
            st.markdown("##### タイトル")
            filtered_title = edited_df.loc[edited_df["id"] == row_id, "タイトル"]
            st.write(filtered_title.iloc[0])
            st.markdown("##### カテゴリ")
            st.write(category_res)
            st.markdown("##### キーワード")
            st.write(keywords_str)
            st.markdown("##### 要約")
            st.write(summary)

            #プログレスバー更新
            progress_bar.progress((i+1) / len(selected_rows))

        # データベースに反映
        # 一時的なテーブル作成
        edited_df.to_sql("temp_metadata", conn, if_exists="replace",index=False)
        # 元データベースから削除する行を特定
        existing_ids=pd.read_sql("SELECT id FROM metadata", conn)["id"]
        new_ids=edited_df["id"]
        ids_to_delete=existing_ids[~existing_ids.isin(new_ids)]
        with conn:
            try:
                # ステートメントの実行
                if not ids_to_delete.empty:
                    conn.execute(f"DELETE FROM metadata WHERE id IN ({','.join(map(str, ids_to_delete))})")

                # `temp_metadata` のレコードを削除
                conn.execute("DELETE FROM metadata WHERE id IN (SELECT id FROM temp_metadata)")

                # データを挿入
                conn.execute("""
                    INSERT INTO metadata (タイトル,著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,カテゴリ,doi,doi_url,ファイルリンク,メモ,Read)
                    SELECT タイトル,著者,ジャーナル,巻,号,開始ページ,終了ページ,年,要約,キーワード,カテゴリ,doi,doi_url,ファイルリンク,メモ,Read FROM temp_metadata
                """)

                # 一時テーブルを削除
                conn.execute("DROP TABLE temp_metadata")

            except sqlite3.OperationalError as err:
                st.error(f"オペレーショナルエラー: {err}")
                
        # データを再読み込みしセッション状態を更新
        df = pd.read_sql("SELECT * From metadata", conn)
        st.session_state["df"]=df
        st.success("変更が保存されました")






if __name__ == "__main__":
    # アプリケーションを実行
    main()
