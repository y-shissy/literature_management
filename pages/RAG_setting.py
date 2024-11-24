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
    st.markdown("#### Google Driveに保存されたPDFファイル")
    pdf_files = drive.ListFile({'q': "mimeType='application/pdf' and trashed=false"}).GetList()
    file_data = []
    for file in pdf_files:
        file_size_mb = round(int(file.get('fileSize', 0)) / 1000000, 1) if 'fileSize' in file else 0.0
        file_data.append({
            'ファイル名': file['title'],
            'ファイルID': file['id'],
            '作成日時': file['createdDate'],
            'ファイルサイズ(MB)': file_size_mb,
        })
    pdf_df = pd.DataFrame(file_data)
    st.dataframe(pdf_df)

    # Google DriveからインデックスZIPリストを取得
    st.markdown("#### 既存のインデックスファイル")
    index_files = drive.ListFile({'q': "title contains '_index.zip' and trashed=false"}).GetList()
    index_file_names = [file['title'] for file in index_files]
    st.write(index_file_names if index_file_names else "インデックスファイルはまだありません。")

    # インデックス化するPDFを選択
    st.markdown("#### インデックス化するPDFを選択")
    pdf_names = [file['title'] for file in pdf_files]

    # 現在選択されているファイルをセッションステートで管理
    if 'selected_files' not in st.session_state:
        st.session_state.selected_files = []

    # ボタンによる選択操作
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("全て選択"):
            st.session_state.selected_files = pdf_names

    with col2:
        if st.button("未実施のみ選択"):
            st.session_state.selected_files = [file for file in pdf_names if f"{file}_index.zip" not in index_file_names]

    with col3:
        if st.button("選択解除"):
            st.session_state.selected_files = []

    # セレクトボックスで選択内容をユーザーが変更できるようにする
    selected_files = st.multiselect("インデックス化するPDFを選択してください:", pdf_names, default=st.session_state.selected_files)
    st.session_state.selected_files = selected_files  # セッションステートを更新

    # インデックス作成処理開始
    if st.button("インデックス生成開始"):
        progress_bar = st.progress(0)
        actual_files_to_index = []

        for file in pdf_files:
            if file['title'] in st.session_state.selected_files:
                actual_files_to_index.append(file)

        for idx, file in enumerate(actual_files_to_index):
            file_title = file['title']
            file_id = file['id']

            # インデックスが既に存在する場合は削除
            if f"{file_title}_index.zip" in index_file_names:
                st.info(f"{file_title}はすでにインデックス化されています。既存のインデックスファイルを削除します。")
                existing_file = [i for i in index_files if i['title'] == f"{file_title}_index.zip"][0]
                existing_file.Delete()

            # Google DriveからPDFファイルをダウンロード
            downloaded_file = drive.CreateFile({'id': file_id})
            temp_pdf_path = os.path.join(tempfile.gettempdir(), file_title)
            downloaded_file.GetContentFile(temp_pdf_path)

            # PDFファイルをテキスト抽出
            documents = extract_text_from_pdf(temp_pdf_path)

            # ベクトル化して保存
            Settings.llm = OpenAI(model="gpt-4o-mini", temperature=0.1)
            Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small", embed_batch_size=100)
            Settings.tokenizer = tiktoken.encoding_for_model("gpt-4o-mini").encode

            index = VectorStoreIndex.from_documents(documents)

            # 一時ディレクトリに保存
            index_dir = tempfile.mkdtemp()
            index.storage_context.persist(persist_dir=index_dir)

            # ZIPファイル作成
            zip_file_path = os.path.join(tempfile.gettempdir(), f"{file_title}_index.zip")
            shutil.make_archive(zip_file_path.replace('.zip', ''), 'zip', index_dir)

            # Google Driveにアップロード
            index_file = drive.CreateFile({'title': f"{file_title}_index.zip"})
            index_file.SetContentFile(zip_file_path)
            try:
                index_file.Upload()
                st.success(f"{file_title}_index.zip がGoogle Driveにアップロードされました！")
            except Exception as e:
                st.error(f"Google Driveへのアップロードに失敗しました: {str(e)}")

            # クリーンアップ
            shutil.rmtree(index_dir)
            os.remove(temp_pdf_path)
            os.remove(zip_file_path)

            # プログレスバー更新
            progress_percent = (idx + 1) / len(actual_files_to_index)
            progress_bar.progress(progress_percent)

        st.success("すべてのインデックスが生成されました！")

if __name__ == "__main__":
    main()