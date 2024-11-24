import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import os
import tempfile
import shutil
import pandas as pd
import openai  # OpenAIライブラリをインポート
from llama_index.core import VectorStoreIndex, Settings
from llama_index.llms.openai import OpenAI  # OpenAIクラスのインポート
from llama_index.embeddings.openai import OpenAIEmbedding
import tiktoken
from function import extract_text_from_pdf

# ページ設定
st.set_page_config(layout="wide")
# Google Drive接続
drive = st.session_state['drive']
# OpenAI APIキーの設定
openai.api_key = st.secrets["openai_api_key"]

# メイン関数
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

    # インデックス作成処理開始
    if st.button("インデックス生成開始"):
        progress_bar = st.progress(0)  # プログレスバーを初期化
        number_of_files = len(file_list)

        for idx, file in enumerate(file_list):
            file_id = file['id']
            file_title = file['title']

            # Google DriveからPDFファイルをダウンロード
            downloaded_file = drive.CreateFile({'id': file_id})
            temp_pdf_path = os.path.join(tempfile.gettempdir(), file_title)
            downloaded_file.GetContentFile(temp_pdf_path)

            # PDFファイルを読み込むために適したメソッドを使用
            documents = extract_text_from_pdf(temp_pdf_path)

            # ベクトル化して保存
            Settings.llm = OpenAI(model="gpt-4o-mini", temperature=0.1)
            Settings.embed_model = OpenAIEmbedding(
                model="text-embedding-3-small", embed_batch_size=100
            )
            Settings.tokenizer = tiktoken.encoding_for_model("gpt-4o-mini").encode

            # インデックスを作成
            index = VectorStoreIndex.from_documents(documents)

            # ストレージへの保存（ディレクトリを指定）
            index_dir = tempfile.mkdtemp()  # 一時ディレクトリを作成
            index.storage_context.persist(persist_dir=index_dir)

            # ZIPファイル作成
            zip_file_path = os.path.join(tempfile.gettempdir(), f"{file_title}_index.zip")
            shutil.make_archive(zip_file_path.replace('.zip', ''), 'zip', index_dir)

            # Google DriveにZIPファイルをアップロード
            index_file = drive.CreateFile({'title': f"{file_title}_index.zip"})
            index_file.SetContentFile(zip_file_path)

            try:
                index_file.Upload()
                st.success(f"{file_title}_index.zip が Google Drive にアップロードされました！")
            except Exception as e:
                st.error(f"Google Driveへのアップロードに失敗しました: {str(e)}")

            # 後処理
            shutil.rmtree(index_dir)  # 一時ディレクトリを削除
            os.remove(zip_file_path)  # ZIPファイルを削除

            # プログレスバーの更新
            progress_percent = (idx + 1) / number_of_files
            progress_bar.progress(progress_percent)

        st.success("すべてのインデックスが生成されました！")  # 処理完了メッセージ

if __name__ == "__main__":
    # アプリケーションを実行
    main()