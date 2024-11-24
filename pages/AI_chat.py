import streamlit as st
from pydrive.drive import GoogleDrive
import tempfile
import shutil
import os
import logging
from llama_index.core import StorageContext, load_index_from_storage
import openai

# ページ設定
st.set_page_config(layout="wide")

# Google Drive接続
drive = st.session_state.get('drive')

# OpenAI APIキーの設定
openai.api_key = st.secrets["openai_api_key"]

def main():
    st.title(":robot_face: AI Chat")
    st.markdown("### 文献PDF情報から情報を検索")

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("__name__")
    logger.debug("調査用ログ")

    # Google DriveからインデックスZIPを取得
    st.write("Google Driveからインデックスを取得中...")
    file_list = drive.ListFile({'q': "title contains '_index.zip' and trashed=false"}).GetList()
    if not file_list:
        st.error("インデックスファイルがGoogle Drive上に見つかりませんでした。")
        return

    # ZIPファイルをすべて解凍し、インデックスを読み込む
    index_list = []
    temp_dirs = []  # 解凍したディレクトリを管理
    for index_file in file_list:
        zip_file_path = os.path.join(tempfile.gettempdir(), index_file['title'])
        index_file.GetContentFile(zip_file_path)

        extract_dir = tempfile.mkdtemp()
        temp_dirs.append(extract_dir)
        shutil.unpack_archive(zip_file_path, extract_dir)
        st.success(f"インデックスファイル {index_file['title']} を解凍しました。")

        # インデックスを読み込む
        try:
            storage_context = StorageContext.from_defaults(persist_dir=extract_dir)
            index = load_index_from_storage(storage_context)
            index_list.append(index)
        except Exception as e:
            st.error(f"{index_file['title']} のインデックス読み込みに失敗しました: {str(e)}")
            shutil.rmtree(extract_dir)  # 解凍ディレクトリを削除
            os.remove(zip_file_path)  # ZIPファイルを削除
            continue

        # ZIPファイルの削除
        os.remove(zip_file_path)

    if not index_list:
        st.error("有効なインデックスが見つかりませんでした。")
        return

    # クエリを各インデックスに対して実行する関数
    def query_indices(query):
        results = []
        for index in index_list:
            try:
                result = index.query(query)  # indexに適したクエリを実行するメソッドがあると仮定
                results.append(result)
            except Exception as e:
                st.error(f"{index} でのクエリ実行に失敗しました: {str(e)}")
        return results

    # チャットリセット
    if st.button("リセット", use_container_width=True):
        st.session_state.messages = [{"role": "assistant", "content": "質問をどうぞ"}]
        st.rerun()
        logger.info("reset")

    # チャット履歴表示
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "assistant", "content": "質問をどうぞ"}]

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # 質問を受け付け
    if prompt_input := st.chat_input():
        prompt = prompt_input + "\nこの質問を日本語と英語の両方で検索し，最も関連性の高い結果を日本語で回答してください．"
        responses = query_indices(prompt)

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        # 各インデックスからの応答を表示
        for idx, res in enumerate(responses):
            msg = str(res)
            st.session_state.messages.append({"role": "assistant", "content": msg})
            with st.chat_message("assistant"):
                st.write(f"インデックス {idx + 1} の結果: {msg}")

    # 解凍したディレクトリをクリーンアップ
    for temp_dir in temp_dirs:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    main()