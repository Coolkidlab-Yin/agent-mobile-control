"""手機介面：用 headless Chromium 模擬 iPhone，對著真的伺服器實際點。
蓋的是這幾週每次改完都得手動重做一遍的三件事：右滑返回（含程式碼區塊裡不能誤觸發）、
看圖層與檔案檢視層（三種關法、返回鍵先關檢視層不關房間）、開房間時早就在等的問題卡要出現（10-06 的洞）。

跑法：check.cmd ui（預設的 check.cmd 跳過這組，因為要起瀏覽器）。缺 Playwright 或 Chromium 就整組 skip。
所有磁碟位置都指到暫存目錄；伺服器在同一個行程裡另起 port，不碰 8899。"""
import os
import re
import socket
import struct
import threading
import time
import zlib
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from helpers import assistant, write_jsonl

from claude_chat import api as A
from claude_chat import perm as P
from claude_chat import rooms as R
from claude_chat.config import CONFIG
from claude_chat.runs import BY_SESSION, RUNS, Run

pw_api = pytest.importorskip("playwright.sync_api")
expect = pw_api.expect

pytestmark = pytest.mark.ui

SID = "dddddddd-0000-0000-0000-000000000004"
SLUG = "C--work-uiproj"
QUESTION = "要推上去嗎？"
LONG_CODE = "x" * 400   # 比 iPhone 寬度長得多的一行 → <pre> 可橫向捲動


def png_bytes(w, h):
    """不靠 Pillow 的最小 PNG（純色），夠大到點得到。"""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x30\x60\xa0" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui")
    if " " in str(tmp):
        pytest.skip("暫存路徑含空白，回覆裡的檔案路徑偵測（FILE_RE）本來就不吃空白")
    mp = pytest.MonkeyPatch()
    proj = tmp / "projects"
    for name in ("LIVE_DIR", "APP_SESSIONS", "WEB_ARCHIVE", "TITLES_FILE", "SNAP_FILE", "CODEX_SESSIONS", "API_CHATS"):
        mp.setattr(R, name, tmp / name.lower())
    mp.setattr(R, "PROJECTS_DIR", proj)
    mp.setattr(P, "PROJECTS_DIR", proj)
    mp.setattr(R, "desktop_registry", lambda: {})
    mp.setattr(A, "_peer_register", lambda: False)   # 不把測試伺服器登記成桌面 app 的 peer
    mp.setitem(CONFIG, "auth_token", "")
    files = tmp / "files"
    files.mkdir()
    mp.setattr(A, "ALLOWED_FILE_ROOTS", (str(files.resolve()).casefold() + "\\",))
    R._room_cache.clear()
    for cache in (R._app_sids_cache, R._titles_cache, R._overlay_cache, R._snap_cache):
        cache["mtime"] = None

    pic = files / "pic.png"
    pic.write_bytes(png_bytes(240, 160))
    note = files / "note.txt"
    note.write_text("這是文件檢視層要顯示的內容", "utf-8")
    recs = [{"type": "user", "cwd": "C:\\work\\uiproj", "entrypoint": "cli",
             "message": {"role": "user", "content": "手機介面測試"}, "timestamp": "2026-10-06T01:00:00Z"}]
    recs.append(assistant("看圖：%s\n\n文件：%s\n\n```\n%s\n```\n\n最後一句。" % (pic, note, LONG_CODE)))
    f = write_jsonl(proj / SLUG / (SID + ".jsonl"), recs)
    old = time.time() - 3600
    os.utime(f, (old, old))   # 剛寫的檔會被當成「桌面工作中」（15 秒內有寫入）而多出打字泡泡，推到一小時前

    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "測試伺服器沒起來"
    yield SimpleNamespace(url="http://127.0.0.1:%d" % port, pic=pic, note=note)
    srv.should_exit = True
    th.join(5)
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    pw = pw_api.sync_playwright().start()
    try:
        b = pw.chromium.launch(headless=True)
    except pw_api.Error as e:
        pw.stop()
        pytest.skip("Chromium 沒裝（python -m playwright install chromium）：%s" % str(e).splitlines()[0])
    device = dict(pw.devices["iPhone 13"])
    device.pop("default_browser_type", None)
    yield SimpleNamespace(browser=b, device=device)
    b.close()
    pw.stop()


@pytest.fixture
def page(browser, server):
    ctx = browser.browser.new_context(**browser.device)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server.url)
    yield pg
    ctx.close()
    P.PERMS.clear()
    assert errors == [], "頁面有 JS 錯誤：%s" % errors


def open_room(page):
    # 首頁是工作台；清單在「活動」面板，先切過去再點
    page.locator("#bottom-nav [data-tab='all']").click()
    expect(page.locator("#pane-all")).to_be_visible()
    page.locator(".room").first.click()
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))
    expect(page.locator("#messages .bubble:not(.typing)")).to_have_count(2)   # 問題卡與打字泡泡不算
    # 對話頁是從右邊滑進來的：動畫沒跑完就去摸，手指落在底下的清單上，對話頁根本沒收到 touch（第一版測試就這樣空過）
    page.wait_for_function("['none', 'matrix(1, 0, 0, 1, 0, 0)'].includes(getComputedStyle(chatScreen).transform)")
    return page.locator("#screen-chat")


def touch_swipe(page, points, step_ms=25, probe=None):
    """真的 touch 事件（page.touchscreen 只有 tap）；一路 touchmove 才會觸發跟手與放開判斷。
    probe：每次 touchmove 後算一次的 JS 表達式，回傳各次的結果，拿來證明手勢真的有被接到（或刻意沒接）。"""
    cdp = page.context.new_cdp_session(page)
    x, y = points[0]
    assert page.evaluate("([x, y]) => chatScreen.contains(document.elementFromPoint(x, y)) || "
                         "!lightbox.classList.contains('hidden') || "
                         "!!document.querySelector('.sheet-mask:not(.hidden) .sheet')?.contains(document.elementFromPoint(x, y))",
                         [x, y]), "起手點不在對話頁、看圖層或面板上"
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    seen = []
    for x, y in points[1:]:
        page.wait_for_timeout(step_ms)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
        if probe:
            seen.append(page.evaluate(probe))
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()
    return seen


def chat_closed(page):
    expect(page.locator("#screen-chat")).to_have_class(re.compile(r"hidden-right"))
    page.wait_for_function("current === null")


# ---------- 右滑返回 ----------

def test_swipe_right_on_messages_returns_to_list(page):
    open_room(page)
    box = page.locator("#messages").bounding_box()
    y = box["y"] + box["height"] - 40   # 訊息下方的空白處
    seen = touch_swipe(page, [(30, y), (60, y), (120, y), (200, y), (280, y)], probe="chatScreen.style.transform")
    assert any(s.startswith("translateX(") for s in seen), "畫面沒有跟手：%s" % seen   # 超過三分之一寬（390/3）就回清單
    chat_closed(page)
    expect(page.locator(".room").first).to_be_visible()


def test_swipe_too_short_snaps_back(page):
    open_room(page)
    box = page.locator("#messages").bounding_box()
    y = box["y"] + box["height"] - 40
    seen = touch_swipe(page, [(30, y), (50, y), (70, y), (90, y)], step_ms=150, probe="chatScreen.style.transform")   # 又短又慢
    assert any(s.startswith("translateX(") for s in seen), "畫面沒有跟手：%s" % seen
    page.wait_for_timeout(300)
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))
    assert page.evaluate("current !== null")
    assert page.evaluate("chatScreen.style.transform") == ""   # 彈回原位


def test_swipe_inside_scrollable_code_block_does_not_close(page):
    """程式碼區塊自己能橫向捲，手勢不接手；瀏覽器的「橫向捲到底就回上一頁」也被關掉（10-03 踩到）。"""
    open_room(page)
    # 原生那個返回手勢在 headless 用合成 touch 重現不出來（突變測試驗過：拿掉樣式這組照樣綠），只能守住樣式本身
    assert page.evaluate("getComputedStyle(document.documentElement).overscrollBehaviorX") == "none"
    pre = page.locator("#messages pre").first
    assert page.evaluate("(el) => el.scrollWidth > el.clientWidth + 2", pre.element_handle()), "程式碼區塊要能橫向捲才測得到"
    box = pre.bounding_box()
    y = box["y"] + box["height"] / 2
    seen = touch_swipe(page, [(box["x"] + 20, y), (box["x"] + 80, y), (box["x"] + 160, y), (box["x"] + 260, y)],
                       probe="chatScreen.style.transform")
    assert seen == ["", "", ""], "程式碼區塊裡的手勢被對話頁接走了：%s" % seen
    page.wait_for_timeout(400)
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))
    assert page.evaluate("current !== null")


# ---------- 看圖層 ----------

def test_image_opens_lightbox_and_closes_by_button_backdrop_and_swipe_down(page):
    open_room(page)
    lightbox = page.locator(".lightbox")
    img = page.locator("#messages .bubble img").first

    img.click()
    expect(lightbox).to_be_visible()
    assert "/api/file?path=" in page.locator(".lightbox img").get_attribute("src")
    assert page.evaluate("history.state && history.state.viewer === 1")
    page.locator(".lb-close").click()
    expect(lightbox).to_be_hidden()
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))

    img.click()
    expect(lightbox).to_be_visible()
    shown = page.locator(".lightbox img").bounding_box()
    page.mouse.click(shown["x"] + shown["width"] / 2, shown["y"] + shown["height"] / 2)   # 點圖＝切換原尺寸
    expect(lightbox).to_have_class(re.compile(r"zoom"))
    page.mouse.click(shown["x"] + shown["width"] / 2, shown["y"] + shown["height"] / 2)
    expect(lightbox).not_to_have_class(re.compile(r"zoom"))
    page.mouse.click(8, 420)   # 黑底
    expect(lightbox).to_be_hidden()

    img.click()
    expect(lightbox).to_be_visible()
    touch_swipe(page, [(20, 300), (20, 360), (20, 420), (20, 480)])   # 往下滑超過 120px
    expect(lightbox).to_be_hidden()
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))


def test_back_navigation_closes_viewer_before_room(page):
    """看圖時按返回（左上鍵、系統邊緣手勢、安卓返回鍵都是 popstate）：先關看圖層，房間要還在；再按一次才回清單。"""
    open_room(page)
    page.locator("#messages .bubble img").first.click()
    expect(page.locator(".lightbox")).to_be_visible()
    page.evaluate("history.back()")
    expect(page.locator(".lightbox")).to_be_hidden()
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))
    assert page.evaluate("current !== null")
    page.evaluate("history.back()")
    chat_closed(page)


# ---------- 檔案檢視層 ----------

def test_text_file_link_opens_docview(page, server):
    open_room(page)
    page.locator("#messages a.file-link", has_text="note.txt").click()
    docview = page.locator(".docview")
    expect(docview).to_be_visible()
    expect(page.locator(".docview .dv-title")).to_have_text("note.txt")
    expect(page.locator(".docview .dv-text")).to_contain_text(server.note.read_text("utf-8"))
    page.locator(".docview .dv-close").click()
    expect(docview).to_be_hidden()
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))


# ---------- 工作台（首頁） ----------

def test_home_opens_on_workbench_with_three_tabs_and_the_room_under_done(page):
    expect(page.locator("#list-title")).to_have_text("工作台")
    expect(page.locator("#home-tabs button")).to_have_count(3)
    expect(page.locator("#home-tabs button.on")).to_have_attribute("data-v", "todo")
    expect(page.locator("#home-list .empty-hint")).to_have_text("沒有在等你的事")
    expect(page.locator("#home-conn")).to_contain_text("已連線")
    page.locator("#home-tabs button[data-v='done']").click()
    # 「全部專案」時已完成不倒卡片，放各專案的按鈕；點了就等於在下拉選單選那個專案
    expect(page.locator(".hcard")).to_have_count(0)
    pick = page.locator(".proj-pick-btn")
    expect(pick).to_have_count(1)
    expect(pick).to_contain_text("uiproj")
    pick.click()
    expect(page.locator("#proj-select")).to_have_value("uiproj")
    card = page.locator(".hcard").first
    expect(card).to_be_visible()
    expect(card.locator(".hc-meta")).to_contain_text("uiproj")
    expect(card.locator(".badge")).to_have_count(0)   # 沒有 run 紀錄就不掛徽章，不編狀態
    card.click()
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))


def test_home_pending_question_shows_in_todo_with_badge_and_cta(page, server):
    r = httpx.post(server.url + "/api/perm", json={
        "session_id": SID, "tool_name": "AskUserQuestion", "event": "PermissionRequest",
        "tool_input": {"questions": [{"question": QUESTION, "options": [{"label": "推"}, {"label": "先不要"}]}]},
    }, timeout=10)
    perm_id = r.json()["perm_id"]
    page.reload()
    expect(page.locator("#home-tabs button[data-v='todo'] i")).to_have_text("1")
    card = page.locator(".hcard.k-ask").first
    expect(card.locator(".badge")).to_have_text("等你回答")
    expect(card.locator(".hc-body")).to_have_text(QUESTION)
    card.locator(".hc-cta").click()
    expect(page.locator("#screen-chat")).not_to_have_class(re.compile(r"hidden-right"))
    expect(page.locator(".ask-card.perm-card[data-perm-id='%s']" % perm_id)).to_be_visible()


def test_project_dropdown_filters_cards_and_remembers_choice(page):
    sel = page.locator("#proj-select")
    expect(sel).to_be_visible()
    expect(sel.locator("option")).to_have_count(2)   # 全部專案 + uiproj
    page.locator("#home-tabs button[data-v='done']").click()
    expect(page.locator(".hcard")).to_have_count(0)          # 全部專案：已完成不列卡片
    expect(page.locator("#home-tabs button[data-v='done'] i")).to_have_text("1")   # 數字照算
    sel.select_option("uiproj")
    expect(page.locator(".hcard")).to_have_count(1)
    assert page.evaluate("localStorage.getItem('cc-proj')") == "uiproj"
    page.reload()
    expect(page.locator("#proj-select")).to_have_value("uiproj")
    expect(page.locator(".hcard")).to_have_count(1)           # 重載後記得專案，已完成直接列
    page.locator("#proj-select").select_option("全部")
    assert page.evaluate("localStorage.getItem('cc-proj')") == "全部"
    expect(page.locator(".proj-pick-btn")).to_have_count(1)   # 切回全部，又變回專案按鈕


def test_bottom_nav_switches_to_all_conversations_and_back(page):
    page.locator("#bottom-nav [data-tab='all']").click()
    expect(page.locator("#list-title")).to_have_text("全部對話")
    expect(page.locator("#pane-all")).to_be_visible()
    expect(page.locator(".room").first).to_be_visible()
    page.locator("#bottom-nav [data-tab='home']").click()
    expect(page.locator("#list-title")).to_have_text("工作台")
    expect(page.locator("#pane-home")).to_be_visible()


# ---------- 問題卡 ----------

def test_pending_question_card_shows_on_room_open_and_answers(page, server):
    """桌面對話早就在等的 AskUserQuestion：清單要有提示，開房間就要看到卡（不靠即時連線），點選項要送回伺服器。"""
    r = httpx.post(server.url + "/api/perm", json={
        "session_id": SID, "tool_name": "AskUserQuestion", "event": "PermissionRequest",
        "tool_input": {"questions": [{"question": QUESTION, "header": "推送",
                                      "options": [{"label": "推", "description": "現在就推上去"},
                                                  {"label": "先不要", "description": "再等等"}]}]},
    }, timeout=10)
    perm_id = r.json()["perm_id"]
    page.reload()
    expect(page.locator("#perm-banner")).to_contain_text("在等你回答問題")
    expect(page.locator(".room .tag.perm")).to_contain_text("等你回答")

    open_room(page)
    card = page.locator(".ask-card.perm-card[data-perm-id='%s']" % perm_id)
    expect(card).to_be_visible()
    expect(card.locator(".ask-head")).to_contain_text("桌面的對話想問你")
    expect(card.locator(".ask-question")).to_have_text(QUESTION)
    expect(card.locator(".ask-opt")).to_have_count(2)
    expect(card.locator(".ask-opt b").first).to_have_text("推")

    card.locator(".ask-opt").first.click()
    expect(card).to_have_class(re.compile(r"done"))
    expect(card.locator(".ask-note")).to_have_text("已回答")
    for _ in range(40):
        if P.PERMS[perm_id]["answer"] is not None:
            break
        time.sleep(0.05)
    p = P.PERMS[perm_id]
    assert p["answer"] == "allow" and p["by"] == "phone" and p["answers"] == {QUESTION: "推"}


def test_pending_question_card_shows_when_room_has_live_run(page, server):
    """10-06 真正的情境：這房間有一條手機端起的即時連線（例如背景重連），卡片是在連線期間送出的。
    開房間時事件流從 n_events 之後接，早先的 ask 事件不會重播——卡片得靠 /api/history 的 pending 補回來，不然只看到轉圈圈。"""
    run = Run("ui-live-run", SLUG, SID, "C:\\work\\uiproj")
    RUNS[run.id] = run
    BY_SESSION[SID] = run.id
    try:
        r = httpx.post(server.url + "/api/perm", json={
            "session_id": SID, "tool_name": "AskUserQuestion", "event": "PermissionRequest",
            "tool_input": {"questions": [{"question": QUESTION, "options": [{"label": "推"}, {"label": "先不要"}]}]},
        }, timeout=10)
        perm_id = r.json()["perm_id"]
        assert len(run.events) == 1 and run.events[0]["kind"] == "perm"   # 卡片已經塞進事件流，接上去時不會再來一次
        h = httpx.get(server.url + "/api/history/%s/%s" % (SLUG, SID), timeout=10).json()
        assert h["run"] == {"run_id": run.id, "n_events": 1, "peer": False}
        page.reload()
        open_room(page)
        card = page.locator(".ask-card.perm-card[data-perm-id='%s']" % perm_id)
        expect(card).to_be_visible()
        expect(card.locator(".ask-opt")).to_have_count(2)
    finally:
        RUNS.pop(run.id, None)
        BY_SESSION.pop(SID, None)


# ---------- 底部面板往下滑關閉 ----------

def open_settings(page):
    page.locator("#btn-settings").click()
    mask = page.locator("#sheet-settings")
    expect(mask).not_to_have_class(re.compile(r"hidden"))
    # 設定面板比 iPhone 螢幕高，才測得到「捲到一半不該關」那條
    assert page.evaluate("(() => { const s = document.querySelector('#sheet-settings .sheet');"
                         " return s.scrollHeight > s.clientHeight + 50; })()")
    return mask


def sheet_point(page, dy=0):
    """面板標題的中心點；標題在面板最頂，起手在這裡保證 scrollTop 是 0 那層的語意"""
    box = page.locator("#sheet-settings .sheet-title").bounding_box()
    return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 + dy)


def test_settings_sheet_closes_on_pull_down(page):
    mask = open_settings(page)
    x, y = sheet_point(page)
    seen = touch_swipe(page, [(x, y), (x, y + 30), (x, y + 90), (x, y + 160), (x, y + 230)],
                       probe="document.querySelector('#sheet-settings .sheet').style.transform")
    assert any(t.startswith("translateY(") for t in seen), "面板沒跟手：%s" % seen
    expect(mask).to_have_class(re.compile(r"hidden"))


def test_settings_sheet_snaps_back_when_pull_is_short(page):
    mask = open_settings(page)
    x, y = sheet_point(page)
    touch_swipe(page, [(x, y), (x, y + 20), (x, y + 40), (x, y + 60)], step_ms=150)   # 又短又慢
    expect(mask).not_to_have_class(re.compile(r"hidden"))
    assert page.evaluate("document.querySelector('#sheet-settings .sheet').style.transform") == ""   # 彈回原位


def test_settings_sheet_scrolled_down_keeps_scrolling_instead_of_closing(page):
    mask = open_settings(page)
    page.evaluate("document.querySelector('#sheet-settings .sheet').scrollTop = 200")
    assert page.evaluate("document.querySelector('#sheet-settings .sheet').scrollTop") > 0
    box = page.locator("#sheet-settings .sheet").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + 60
    seen = touch_swipe(page, [(x, y), (x, y + 30), (x, y + 90), (x, y + 160), (x, y + 230)],
                       probe="document.querySelector('#sheet-settings .sheet').style.transform")
    assert not any(t.startswith("translateY(") for t in seen), "捲到一半往下滑不該被當成關閉：%s" % seen
    expect(mask).not_to_have_class(re.compile(r"hidden"))
