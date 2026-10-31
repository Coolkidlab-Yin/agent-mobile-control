"""進入點：監聽被關掉要在同一個位址補開。"""
import asyncio

import server as S


def test_listener_closed_by_proactor_is_reopened(monkeypatch):
    """Proactor 接連線遇到 OSError（如 WinError 64）會把監聽 socket 關掉、行程照活，127.0.0.1 從此連不上。
    這裡照它的做法直接關掉 uvicorn 的監聽 socket，看門員要在同一個位址補開一個。"""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    made = []

    class Rec(S.uvicorn.Server):
        def __init__(self, config):
            super().__init__(config)
            made.append(self)

    monkeypatch.setattr(S, "PORT", port)
    monkeypatch.setattr(S, "LISTENER_CHECK_S", 0.2)
    monkeypatch.setattr(S.uvicorn, "Server", Rec)

    async def connect_ok():
        try:
            _, w = await asyncio.open_connection("127.0.0.1", port)
        except ConnectionRefusedError:
            return False
        w.close()
        return True

    async def scenario():
        serve = asyncio.ensure_future(S._serve("127.0.0.1", lifespan="off"))
        for _ in range(50):
            if made and made[0].started:
                break
            await asyncio.sleep(0.05)
        assert await connect_ok()
        for srv in made[0].servers:
            for sock in srv._sockets:
                sock.close()   # 跟 proactor_events 遇到 OSError 時的 sock.close() 一樣
        assert not await connect_ok()
        for _ in range(40):
            await asyncio.sleep(0.1)
            if len(made) > 1 and made[-1].started and await connect_ok():
                break
        assert len(made) == 2 and made[1].config.lifespan == "off" and await connect_ok()
        for m in made:
            m.should_exit = True
        await asyncio.wait_for(serve, 5)

    asyncio.run(scenario())
