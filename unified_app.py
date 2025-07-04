import streamlit as st
import uuid
import base64
import types
import torch
import json
from pathlib import Path

# Ensure Streamlit page configuration is applied once across modules
if "_page_configured" not in st.session_state:
    st.set_page_config(
        page_title="Unified Knowledge Upload",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.session_state["_page_configured"] = True

# Workaround: avoid Streamlit watcher errors with torch dynamic modules
if hasattr(torch, "classes") and not hasattr(torch.classes, "__path__"):
    torch.classes.__path__ = types.SimpleNamespace(_path=[])

from knowledge_gpt_app.app import (
    read_file,
    semantic_chunking,
    get_openai_client,
    refresh_search_engine,
    apply_intel_theme,
    list_knowledge_bases,
    search_multiple_knowledge_bases,
)
from knowledge_gpt_app.gpt_handler import (
    generate_gpt_response,
    get_persona_list,
)
from mm_kb_builder.app import (
    process_cad_file,
    encode_image_to_base64,
    analyze_image_with_gpt4o,
    create_comprehensive_search_chunk,
    get_embedding,
    save_unified_knowledge_item,
    SUPPORTED_IMAGE_TYPES,
    SUPPORTED_CAD_TYPES,
)
from generate_faq import generate_faqs_from_chunks
from shared.upload_utils import BASE_KNOWLEDGE_DIR


def extract_mid_text(text: str, length: int = 12) -> str:
    """Return a short snippet from the middle of the text."""
    text = text.strip().replace("\n", " ")
    if len(text) <= length:
        return text
    mid = len(text) // 2
    start = max(0, mid - length // 2)
    return text[start : start + length]


def add_thumbnail(item_id: str, item_type: str, content: str) -> None:
    """Store thumbnail info in session state."""
    data = {"id": item_id, "type": item_type, "content": content}
    st.session_state.setdefault("thumbnails", []).append(data)


def display_thumbnails(kb_name: str) -> None:
    """Render thumbnails in a 3x3 grid with simple paging."""
    thumbs = st.session_state.get("thumbnails", [])
    if not thumbs:
        return

    page = st.session_state.get("thumb_page", 0)
    start = page * 9
    end = start + 9
    page_items = thumbs[start:end]

    for row in range(3):
        cols = st.columns(3)
        for col in range(3):
            idx = row * 3 + col
            if idx >= len(page_items):
                break
            item = page_items[idx]
            if item["type"] == "image":
                img_bytes = base64.b64decode(item["content"])
                cols[col].image(img_bytes, use_column_width=True)
            else:
                cols[col].markdown(
                    f"<div style='font-size:10pt'>{item['content']}</div>",
                    unsafe_allow_html=True,
                )
            if cols[col].button("メタ情報入力", key=f"meta_btn_{item['id']}"):
                st.session_state["edit_target"] = item

    nav_cols = st.columns(2)
    if page > 0:
        if nav_cols[0].button("前へ", key="prev_page"):
            st.session_state["thumb_page"] = page - 1
    if end < len(thumbs):
        if nav_cols[1].button("次へ", key="next_page"):
            st.session_state["thumb_page"] = page + 1

    if "edit_target" in st.session_state:
        item = st.session_state["edit_target"]
        st.subheader("メタ情報編集")
        title = st.text_input("タイトル", key=f"title_{item['id']}")
        tags = st.text_input("タグ (カンマ区切り)", key=f"tags_{item['id']}")
        if st.button("保存", key=f"save_meta_{item['id']}"):
            meta = {
                "title": title,
                "tags": [t.strip() for t in tags.split(",") if t.strip()],
            }
            meta_dir = BASE_KNOWLEDGE_DIR / kb_name / "metadata"
            meta_dir.mkdir(parents=True, exist_ok=True)
            path = meta_dir / f"{item['id']}_user.json"
            path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            st.success("メタ情報を保存しました")
            del st.session_state["edit_target"]

# Apply common theme styling
apply_intel_theme()

st.title("KnowledgePlus")

menu_options = ["GPTモード", "ナレッジ検索", "ナレッジ構築"]
if "nav_mode" not in st.session_state:
    st.session_state["nav_mode"] = "ナレッジ検索"
menu_index = menu_options.index(st.session_state["nav_mode"])
mode = st.sidebar.radio("メニュー", menu_options, index=menu_index)
st.session_state["nav_mode"] = mode

if mode == "GPTモード":
    st.header("GPTチャット")
    st.sidebar.header("チャット設定")
    personas = get_persona_list()
    persona_names = [p["name"] for p in personas]
    persona_map = {p["name"]: p["id"] for p in personas}
    current_persona = st.session_state.get("chat_persona", persona_names[0] if persona_names else "")
    idx = persona_names.index(current_persona) if current_persona in persona_names else 0
    sel_name = st.sidebar.selectbox("AIペルソナ", persona_names, index=idx)
    st.session_state["chat_persona"] = sel_name
    temp = st.sidebar.slider("温度", 0.0, 1.0, float(st.session_state.get("chat_temp", 0.7)), 0.05)
    st.session_state["chat_temp"] = temp
    resp_opts = ["簡潔", "普通", "詳細"]
    resp_len = st.sidebar.radio("応答の長さ", resp_opts, index=resp_opts.index(st.session_state.get("chat_resp_len", "普通")), horizontal=True)
    st.session_state["chat_resp_len"] = resp_len

    messages = st.session_state.get("chat_messages", [])
    for m in messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_input = st.chat_input("メッセージを入力")
    if user_input:
        messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)
        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.markdown("⟲ 考え中...")
            client = get_openai_client()
            if client:
                reply = generate_gpt_response(
                    user_input,
                    conversation_history=[m for m in messages[:-1] if m["role"] in ["user", "assistant"]],
                    persona=persona_map.get(sel_name, "default"),
                    temperature=temp,
                    response_length=resp_len,
                    client=client,
                )
                placeholder.markdown(reply)
            else:
                reply = "OpenAI client unavailable"
                placeholder.error(reply)
        messages.append({"role": "assistant", "content": reply})
        st.session_state["chat_messages"] = messages

elif mode == "ナレッジ検索":
    st.header("ナレッジ検索")
    kb_list = list_knowledge_bases()
    kb_names = [kb["name"] for kb in kb_list]
    if not kb_names:
        st.info("利用可能なナレッジベースがありません。")
    else:
        selected_kbs = st.multiselect("検索対象ナレッジベース", kb_names, default=kb_names[:1])
        query = st.text_input("検索クエリ")
        if st.button("検索実行"):
            results, not_found = search_multiple_knowledge_bases(query, selected_kbs)
            if not results:
                st.info("検索結果が見つかりませんでした。")
            else:
                for r in results:
                    st.markdown(f"**KB: {r.get('kb_name','N/A')} / 類似度: {r.get('similarity',0):.2f}**")
                    st.text_area("チャンク内容", r.get('text',''), height=120)

if mode == "ナレッジ構築":
    kb_name = st.text_input("Knowledge Base Name", "unified_kb")

    st.sidebar.header("Actions")
    max_tokens = st.sidebar.number_input("Max GPT tokens", 100, 4000, 1000, 100)
    num_pairs = st.sidebar.number_input("Q&A pairs", 1, 10, 3, 1)
    if st.sidebar.button("FAQ生成"):
        client = get_openai_client()
        if not client:
            st.sidebar.error("OpenAI client unavailable")
        else:
            with st.spinner("Generating FAQs..."):
                count = generate_faqs_from_chunks(kb_name, max_tokens, num_pairs, client=client)
                refresh_search_engine(kb_name)
            st.sidebar.success(f"{count} FAQs created")

    all_types = [
        'pdf', 'docx', 'xlsx', 'xls', 'txt', 'md', 'html', 'htm'
    ] + SUPPORTED_IMAGE_TYPES + SUPPORTED_CAD_TYPES
    uploaded_files = st.file_uploader(
        "Upload Files",
        type=all_types,
        accept_multiple_files=True,
    )

    process_mode = st.radio(
        "処理モード",
        ["個別処理", "まとめて処理"],
        horizontal=True,
    )
    index_mode = st.radio(
        "インデックス更新",
        ["自動(処理後)", "手動"],
        horizontal=True,
    )
    if index_mode == "手動":
        if st.button("検索インデックス更新"):
            refresh_search_engine(kb_name)
            st.success("検索インデックスを更新しました")
    auto_faq = st.checkbox("処理後にFAQも生成する")

    if uploaded_files and st.button("Process Files"):
        client = get_openai_client()
        if not client:
            st.error("OpenAI client unavailable")
        else:
            auto_update = index_mode == "自動(処理後)"
            batch = process_mode == "まとめて処理"
            for file in uploaded_files:
                with st.spinner(f"Processing {file.name}..."):
                    ext = file.name.split('.')[-1].lower()
                    bytes_data = file.getvalue()
                    file.seek(0)
                    if ext in ['pdf', 'docx', 'xlsx', 'xls', 'txt', 'md', 'html', 'htm']:
                        text = read_file(file)
                        if text:
                            semantic_chunking(
                                text,
                                15,
                                'C',
                                'auto',
                                kb_name,
                                client,
                                original_filename=file.name,
                                original_bytes=bytes_data,
                                refresh=auto_update and not batch,
                            )
                            add_thumbnail(str(uuid.uuid4()), "text", extract_mid_text(text))
                            st.success(f"Processed text file {file.name}")
                        else:
                            st.error(f"Failed to read {file.name}")
                    elif ext in SUPPORTED_IMAGE_TYPES + SUPPORTED_CAD_TYPES:
                        if ext in SUPPORTED_CAD_TYPES:
                            img_b64, cad_meta = process_cad_file(file, ext)
                            if img_b64 is None:
                                st.error(f"CAD processing failed: {cad_meta.get('error')}")
                                continue
                        else:
                            img_b64 = encode_image_to_base64(file)
                            cad_meta = None
                        analysis = analyze_image_with_gpt4o(img_b64, file.name, cad_meta, client)
                        if "error" in analysis:
                            st.error(f"Analysis failed for {file.name}: {analysis['error']}")
                            continue
                        chunk = create_comprehensive_search_chunk(analysis, {})
                        embedding = get_embedding(chunk, client)
                        if embedding is None:
                            st.error(f"Embedding failed for {file.name}")
                            continue
                        item_id = str(uuid.uuid4())
                        success, _ = save_unified_knowledge_item(
                            item_id,
                            analysis,
                            {},
                            embedding,
                            file.name,
                            img_b64,
                            original_bytes=bytes_data,
                            refresh=auto_update and not batch,
                        )
                        if success:
                            add_thumbnail(item_id, "image", img_b64)
                            st.success(f"Processed image/CAD file {file.name}")
                        else:
                            st.error(f"Saving failed for {file.name}")
                    else:
                        st.warning(f"Unsupported file type: {file.name}")

            if batch and auto_update:
                refresh_search_engine(kb_name)
            if auto_faq:
                with st.spinner("Generating FAQs..."):
                    count = generate_faqs_from_chunks(kb_name, max_tokens, num_pairs, client=client)
                    if auto_update:
                        refresh_search_engine(kb_name)
                st.success(f"{count} FAQs created")
            if not auto_update:
                st.info("処理後は『検索インデックス更新』ボタンを押してください")

    display_thumbnails(kb_name)
