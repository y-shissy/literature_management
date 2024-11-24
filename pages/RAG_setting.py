import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import sqlite3
import os
import tempfile
import pandas as pd
from llama_index.core import download_loader, VectorStoreIndex, Settings, SimpleDirectoryReader,StorageContext,load_index_from_storage
from llama_index.core.node_parser import SentenceSplitter
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
import tiktoken
import pytesseract
from pdf2image import convert_from_path
# 関数読込

from function import store_metadata_in_db, handle_pdf_upload,store_metadata_in_db_ai,download_file,extract_text_from_pdf,translate_and_summarize,pdf_to_text_with_ocr_per_page_multi_lang

# ページ設定
st.set_page_config(layout="wide")
#Google drive
drive=st.session_state['drive']

def main():
    st.title(":robot_face: RAG Setting")
    st.markdown("### PDFファイルからllama_indexを用いてインデックス生成")

    # Google DriveからPDFファイルリストを取得
    file_data = []
    file_list = drive.ListFile({'q': "mimeType='application/pdf' and trashed=false"}).GetList()
    for file in file_list:
        file_size_mb = round(int(file.get('fileSize', 0)) / 1000000, 1) if 'fileSize' in file else 0.0

        file_info = {
            'ファイル名': file['title'],
            'ファイルID': file['id'],
            '作成日時': file['createdDate'],
            'ファイルサイズ(MB)': file_size_mb,
        }
        file_data.append(file_info)

    if not file_data:
        st.write("PDFファイルが見つかりませんでした")
    else:
        df = pd.DataFrame(file_data)
        st.dataframe(df)

    # index作成処理開始
    if st.button("インデックス生成開始"):
        for file in file_list:
            file_id = file['id']
            file_title = file['title']

            # Google DriveからPDFファイルをダウンロード
            downloaded_file = drive.CreateFile({'id': file_id})
            temp_pdf_path = os.path.join(tempfile.gettempdir(), file_title)
            downloaded_file.GetContentFile(temp_pdf_path)

            # PDFファイルを読み込むために適したメソッドを使用
            # PDFファイルのテキストを取得する。
            documents = extract_text_from_pdf(temp_pdf_path)  # PDFからのテキスト抽出処理の呼び出し

            ocr_cache = {}
            # llama_indexで抽出したドキュメントが空の場合はOCR適用
            for doc in documents:
                if doc.text.strip() != "":
                    continue

                file_path = doc.metadata['file_path']
                page_label = doc.metadata.get('page_label')
                # ファイルがキャッシュにない場合，OCRを実行しページごとに結果を保存
                if file_path not in ocr_cache:
                    ocr_cache[file_path] = pdf_to_text_with_ocr_per_page_multi_lang(file_path)

            # ベクトル化して保存
            Settings.llm = OpenAI(model="gpt-4o-mini", temperature=0.1)
            Settings.embed_model = OpenAIEmbedding(
                model="text-embedding-3-small", embed_batch_size=100
            )
            Settings.tokenizer = tiktoken.encoding_for_model("gpt-4o-mini").encode
            # インデックスを作成
            index = VectorStoreIndex.from_documents(documents)

            # ストレージへの保存（Google Driveにアップロード）
            index_file_path = f"{file_title}_index.json"
            index.storage_context.persist(persist_dir=index_file_path)

            # Google Driveにインデックスファイルをアップロード
            index_file = drive.CreateFile({'title': index_file_path})
            index_file.SetContentFile(index_file_path)
            index_file.Upload()

            # 一時ファイルの削除
            os.remove(temp_pdf_path)
            os.remove(index_file_path)  # インデックスファイルも削除

if __name__ == "__main__":
    # アプリケーションを実行
    main()