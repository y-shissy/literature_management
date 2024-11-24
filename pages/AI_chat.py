import streamlit as st
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import sqlite3
import os
import tempfile
import logging
from llama_index.core import StorageContext,load_index_from_storage

# 関数読込
from function import store_metadata_in_db, handle_pdf_upload,store_metadata_in_db_ai,download_file,extract_text_from_pdf,translate_and_summarize

# ページ設定
st.set_page_config(layout="wide")
#Google drive
drive=st.session_state['drive']

DB_FILE = "literature_database.db"

# メイン処理
def main():
    st.title(":robot_face: AI chat")
    st.markdown("### 文献PDF情報から情報を検索")

    logging.basicConfig(level=logging.INFO)
    logger=logging.getLogger("__name__")
    logger.debug("調査用ログ")

    # インデックスデータを読み込む
    storage_context=StorageContext.from_defaults(persist_dir=persit_dir)
    index = load_index_from_storage(storage_context)

    # クエリエンジン設定
    query_engine=index.as_query_engine()

    if st.button("リセット",use_container_width=True):
        st.session_state.messages=[{"role": "assistant", "content": "質問をどうぞ"}]
        st.rerun()
        logger.info("reset")

    if "messages" not in st.session_state.messages:
        st.chat_message(msg["role"].write(msg["content"]))

    if prompt_input := st.chat_input():
        prompt=prompt_input + "\nこの質問を日本語と英語の両方で検索し，最も関連性の高い結果を日本語で回答してください．"
        response = query_engine.query(prompt)
        st.session_state.messages.append({"role": "user", "content":prompt})
        st.chat_message("user").write(prompt)
        msg=str(response)
        st.session_state.messages.append({"role": "assistant", "content":msg})
        st.chat_message("assistant").write(msg)

if __name__ == "__main__":
    # アプリケーションを実行
    main()
