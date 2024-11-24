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
            indices.append({"name": index_file["title"].replace("_index.zip", ""), "index": index})

            # 後始末
            os.remove(zip_file_path)
        except Exception as e:
            st.error(f"インデックスファイル {index_file['title']} の読み込みに失敗しました: {str(e)}")

    return indices

def query_indices(prompt, indices):
    """各インデックスに対してクエリを実行し、結果と参照情報を取得"""
    results = []
    for idx, item in enumerate(indices):
        try:
            query_engine = item["index"].as_query_engine()
            result = query_engine.query(prompt)  # クエリを実行
            results.append({"source": item["name"], "content": result})
        except Exception as e:
            st.error(f"{item['name']} でのクエリ実行に失敗しました: {str(e)}")
    return results

def format_results(results):
    """結果をフォーマットし、関連性の高いものを優先表示"""
    # 結果をランク付け (例として、最初の結果を最重要と仮定)
    sorted_results = sorted(results, key=lambda x: len(str(x["content"])), reverse=True)

    top_results = sorted_results[:1]  # 最も関連性の高い結果
    other_results = sorted_results[1:]  # 残りの結果

    # 最も関連性の高い結果を表示
    st.subheader("📌 最も関連性の高い結果")
    for res in top_results:
        st.write(f"**文献名**: {res['source']}")
        st.write(res["content"])

    # その他の結果を折りたたみ形式で表示
    if other_results:
        with st.expander("📚 他の関連文献を見る"):
            for res in other_results:
                st.write(f"**文献名**: {res['source']}")
                st.write(res["content"])

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

        if responses:
            st.session_state.messages.append({"role": "assistant", "content": "検索結果を表示中..."})
            format_results(responses)

if __name__ == "__main__":
    main()
