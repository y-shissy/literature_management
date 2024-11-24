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

# キャッシュ変数を初期化
if "loaded_indices" not in st.session_state:
    st.session_state["loaded_indices"] = []

def load_indices_from_drive():
    """Google Driveからインデックスを読み込み、キャッシュする"""
    file_list = drive.ListFile({'q': "title contains '_index.zip' and trashed=false"}).GetList()
    if not file_list:
        st.error("インデックスファイルがGoogle Drive上に見つかりませんでした。")
        return []

    indices = []
    for index_file in file_list:
        try:
            # ZIPファイルをダウンロードして解凍
            zip_file_path = os.path.join(tempfile.gettempdir(), index_file['title'])
            index_file.GetContentFile(zip_file_path)

            extract_dir = tempfile.mkdtemp()
            shutil.unpack_archive(zip_file_path, extract_dir)

            # インデックスを読み込む
            storage_context = StorageContext.from_defaults(persist_dir=extract_dir)
            index = load_index_from_storage(storage_context)

            # 読み込んだインデックスをキャッシュに追加
            indices.append(index)

            # 後始末
            os.remove(zip_file_path)
        except Exception as e:
            st.error(f"インデックスファイル {index_file['title']} の読み込みに失敗しました: {str(e)}")

    return indices

def query_indices(prompt, indices):
    """各インデックスに対してクエリを実行し、結果を取得"""
    results = []
    for idx, index in enumerate(indices):
        try:
            query_engine = index.as_query_engine()
            result = query_engine.query(prompt)
            results.append(f"インデックス {idx + 1}: {result}")
        except Exception as e:
            results.append(f"インデックス {idx + 1} でクエリ実行に失敗: {str(e)}")
    return results

def main():
    st.title(":robot_face: AI Chat")
    st.markdown("### 文献PDF情報から情報を検索")

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("__name__")
    logger.debug("調査用ログ")

    # インデックスのロード（キャッシュ利用）
    if not st.session_state["loaded_indices"]:
        with st.spinner("インデックスを読み込んでいます..."):
            st.session_state["loaded_indices"] = load_indices_from_drive()
    
    if not st.session_state["loaded_indices"]:
        st.error("有効なインデックスが見つかりませんでした。")
        return

    indices = st.session_state["loaded_indices"]

    # チャットリセット
    if st.button("リセット", use_container_width=True):
        st.session_state.messages = [{"role": "assistant", "content": "質問をどうぞ"}]
        st.experimental_rerun()
        logger.info("reset")

    # チャット履歴表示
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "assistant", "content": "質問をどうぞ"}]

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # 質問を受け付け
    if prompt_input := st.chat_input():
        prompt = prompt_input + "\nこの質問を日本語と英語の両方で検索し，最も関連性の高い結果を日本語で回答してください。"
        responses = query_indices(prompt, indices)

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        for res in responses:
            st.session_state.messages.append({"role": "assistant", "content": res})
            with st.chat_message("assistant"):
                st.write(res)

if __name__ == "__main__":
    main()
