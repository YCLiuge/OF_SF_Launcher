"""Exercise the production remote frontend's field filter against the selected live test realm."""
import base64
import socket
import struct
from threading import Event, Thread
from types import SimpleNamespace


def run(remote, grant, state, config, accounts):
    from contextlib import ExitStack
    from network.msg_id import MsgId
    from proto import net_pb2 as pb
    from local_game_wire import encode, receive
    from private_chat_text import ProtectedChat
    from realm_gm import operator

    image=base64.b64decode("UklGRiQAAABXRUJQVlA4TBcAAAAvP8EsAAcQ/Y/+BwAU6f9/iuh/6v8PAAA=")
    raw=image[8:]+b"XMP "+struct.pack("<I",6)+b"secret"
    tagged=b"RIFF"+struct.pack("<I",len(raw))+raw
    host=SimpleNamespace(closing=Event(),handlers=vars(MsgId).values(),stages=SimpleNamespace(message=lambda _:None))
    with ExitStack() as stack:
        peer=stack.enter_context(remote.connect(grant))
        peer.chat_guard=ProtectedChat("channel-proof", "local-only-secret")
        a,b=socket.socketpair()
        for s in (a,b):stack.enter_context(s)
        a.settimeout(10)
        def play():
            try:peer.play(b,host)
            except (EOFError,OSError):pass
        worker=Thread(target=play,daemon=True);worker.start()
        sequence=0
        def call(name,**fields):
            nonlocal sequence
            sequence+=1
            request=getattr(pb,name+"Req")(**fields)
            a.sendall(encode(getattr(MsgId,name+"Req"),request.SerializeToString(),sequence))
            events=[]
            for _ in range(300):
                head,data=receive(a)
                if head.packet_id==sequence and head.msg_id==getattr(MsgId,name+"Rsp"):
                    return getattr(pb,name+"Rsp").FromString(data), events
                if head.msg_id==MsgId.GmNotice:
                    events.extend(pb.GmNotice.FromString(data).notice)
            raise AssertionError(name)
        try:
            login,_=call("PlayerLogin",device_uuid="local-only-secret")
            assert login.player_name=="测试玩家"
            main,_=call("PlayerMainData")
            assert main.player_id==1000001 and main.player_name=="测试玩家"
            assert call("SendChatMsg",type=pb.ChatChannel_Default,text="/gm whoami")[0].status==pb.StatusCode_FAIL
            assert operator(state,"grant",uid=1000001)["changed"]
            assert call("GmCode",param="whoami")[0].result.endswith("1000001")
            assert call("SendChatMsg",type=pb.ChatChannel_Default,text="/gm whoami")[0].text.endswith("1000001")
            announced,events=call("SendChatMsg",type=pb.ChatChannel_Default,text="/gm announce 测试公告")
            assert announced.status==pb.StatusCode_OK
            if "测试公告" not in events:
                # Use the next completed request as the delivery boundary.
                extra=call("GmCode",param="whoami")[1]
                assert "测试公告" in extra
            assert call("GmCode",param="announce local-only-secret")[0].status==pb.StatusCode_BAD_REQ
            assert call("ChangeNickName",nick_name="改名玩家",birthday="2002-03-12")[0].status==pb.StatusCode_OK
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect((state/"game.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
                assert db.execute("SELECT player_name FROM players").fetchone()==("改名玩家",)
            assert call("UploadPhotoShare",file=b"not an image",title="坏图")[0].status==pb.StatusCode_BAD_REQ
            assert call("UploadPhotoShare",file=image,title="local-only-secret")[0].status==pb.StatusCode_BAD_REQ
            upload,_=call("UploadPhotoShare",file=tagged,title="花园手记",tag1="春",tag2="海边",camera_param=pb.CameraParameters(camera_version=3))
            assert upload.status==pb.StatusCode_OK and upload.info.title=="花园手记" and upload.info.tag1=="春",upload
            code=upload.info.code
            assert code>0 and upload.info.camera_param.camera_version==3
            with accounts.connection(config) as http:
                http.request("GET","/private/photos/"+str(code))
                res=http.getresponse()
                assert res.status==200 and res.read()==image
            rename,_=call("ChangePhotoShareTitle",code=code,title="花园新篇",tag1="夕阳")
            assert rename.status==pb.StatusCode_OK and rename.title=="花园新篇"
            found,_=call("PhotoShareSearch",search_type=pb.PhotoSearchType_SEARCH,text_type=pb.PhotoSearchTextType_TEXT,text="夕阳")
            assert found.status==pb.StatusCode_OK and [p.code for p in found.photos]==[code]
        finally:
            a.shutdown(socket.SHUT_RDWR);host.closing.set();worker.join(5)
            assert not worker.is_alive()
