"""Player-chosen progress file, redacted preview, account scoping and reversible application."""
import json
from pathlib import Path
from unittest.mock import patch
import zipfile


def backup(folder):
    folder.mkdir()
    marker="unrelated-private-test-data"
    main={"status":{"v":1},"level":2,"exp":1,"world_level":0,"player_id":3128407,"player_label":9067314,"player_name":"群星校验",
          "pet_instance_id":986654322}
    data={"format":"overfield-snapshot/1","account":{"session":marker,"uid":marker,"username":marker,"playerId":3128407,"playerLabel":9067314,"playerName":"群星校验"},"endpoint":marker,
          "decoded":{"main":{"name":"PlayerMainDataRsp","frames":[{"ok":True,"decoded":main}]}},
          "stats":{"other":marker}}
    pet={"item_id":200001009,"instance_id":986654322,"name":"测试云朵","level":1,"nature_id":132,
         "integrity_id":100030,"integrity":[{"attr":{"v":5},"value":14}],"size":243,"weight":162840}
    data["decoded"]["pet"]={"name":"GetPetRsp","frames":[{"ok":True,"decoded":{"status":{"v":1},"total_num":3,"pets":[pet]}}]}
    (folder/"snapshot.json").write_text(json.dumps(data),encoding="utf8")
    (folder/"private-server-import.json").write_text(json.dumps({"irrelevant":marker}),encoding="utf8")
    archive=folder/"player.zip"
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as target:
        for name in ("snapshot.json","private-server-import.json"):
            target.write(folder/name,"source/"+name)
    return archive


def privacy(accounts,folder):
    archive=backup(folder)
    token="ofi_"+"A"*43
    preview="ofv_"+"a"*32
    expected={"format":"of-sf-progress/1","player":[2,1,0],"heroes":[],"quests":[],"chapters":[],
              "achievements":[],"stacks":[],"outfits":[],"gear":[],"team":[0,0,0],"loadouts":[],
              "identity":{"uid":9067314,"nickname":"群星校验"},
              "pets":{"total":3,"capacity":0,"entries":[{"item_id":200001009,"level":1,"nature_id":132,
                      "integrity_id":100030,"star":0,"catch_scene_id":0,"catch_area_id":0,"catch_time":0,
                      "size":243,"weight":162840,"integrity":[[5,14]],"special_color":False,
                      "name":"测试云朵","active":True}]}}
    def capture(config,verb,path,fields=None,max_bytes=0):
        if verb=="GET":
            assert path=="/local/v1/region" and fields is None
            return 200,{"realm":accounts.PRIVATE_REALM,"save_import_features":["pets","field_progress","public_uid_label"]}
        assert verb=="POST" and path=="/local/v1/accounts/import-preview"
        assert fields=={"import_ticket":token,"data":dict(expected,identity={"public_label":9067314,"nickname":"群星校验"})}
        assert "unrelated-private-test-data" not in json.dumps(fields)
        assert "986654322" not in json.dumps(fields)
        return 200,{"ok":True,"eligible":True,"counts":{"角色":1},"warnings":[],"preview_id":preview}
    with patch.object(accounts,"request",side_effect=capture) as requested:
        for source in (archive,folder,folder/"snapshot.json",folder/"private-server-import.json"):
            result=accounts.import_backup("import-preview",{"path":str(source),"import_ticket":token},{})
            assert result["ok"] and result["preview_id"]==preview
        bad=folder/"broken.zip";bad.write_bytes(b"broken")
        reply=accounts.import_backup("import-preview",{"path":str(bad),"import_ticket":token},{})
        assert reply["code"]=="invalid_backup"
        assert requested.call_count==8
    baseline={k:v for k,v in expected.items() if k!="pets"}
    extended=dict(expected,field_progress={"scene":1,"devices":[[1,2]],"riddles":[[1,3]],"challenges":[],
                                           "collectibles":[42],"goals":[]})
    def legacy(config,verb,path,fields=None,max_bytes=0):
        if verb=="GET":
            return 200,{"realm":accounts.PRIVATE_REALM}
        assert fields=={"import_ticket":token,"data":baseline}
        return 200,{"ok":True,"eligible":True,"counts":{},"warnings":[],"preview_id":preview}
    with patch.object(accounts,"request",side_effect=legacy),patch("save_import_data.project_file",return_value=extended.copy()):
        result=accounts.import_backup("import-preview",{"path":str(archive),"import_ticket":token},{})
        assert result["ok"] and any("3 只" in s and "1 只明细" in s for s in result["warnings"])
        assert result["counts"]=={"旧服跳过的宠物明细":1,"旧服跳过的区域进度":3}
        assert any("区域装置 1 条、谜题 1 条、收藏点 1 条" in s for s in result["warnings"])
        assert any("取消" in s for s in result["warnings"])
    with patch.object(accounts,"request",return_value=(200,{"realm":"unrelated"})) as rejected:
        result=accounts.import_backup("import-preview",{"path":str(archive),"import_ticket":token},{})
        assert result["code"]=="import_capabilities_unavailable" and rejected.call_count==1
    return archive


def window_smoke(accounts, config, archive, token):
    import os
    import subprocess
    import sys
    if os.name!="nt":
        return False
    directory=Path(__file__).resolve().parent
    checkout=directory.parent
    checkout=Path(os.environ.get("OF_SF_SERVER_CHECKOUT") or checkout)
    shared=checkout/"client_tools/sources/ImportWindow.cs"
    if not shared.is_file():
        shared=checkout/"kit/client_tools/sources/ImportWindow.cs"
    target=archive.parent/"import-window-smoke.exe"
    compiler=Path(os.environ["WINDIR"])/"Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler),"/nologo","/utf8output","/target:exe","/platform:x64","/r:System.Windows.Forms.dll",
                    "/r:System.Drawing.dll","/r:System.Web.Extensions.dll","/out:"+str(target),str(shared),
                    str(directory/"test_import_window.cs")],check=True)
    payload={"server":config,"python":sys.executable,"api":str(Path(accounts.__file__).resolve()),"ticket":token,"backup":str(archive)}
    subprocess.run([str(target)],input=json.dumps(payload,ensure_ascii=True).encode("ascii"),check=True,timeout=55)
    return True


def over_tls(accounts, private, config, archive, who, state):
    from contextlib import closing
    import sqlite3
    previous=private("login",**who)
    existing=private("import-preview",import_ticket=previous["import_ticket"],path=str(archive))
    assert existing["ok"] and not existing["eligible"]
    who={"username":"backup_target","password":"temporary-backup-target-password"}
    target=private("register",**who,nickname="导入验收")
    ticket=target["import_ticket"]
    assert target["import_expires_in"]>0
    # Use a different private account to check a preview cannot be applied across slots.
    other=private("register",username="other_import_test",password="backup-test-password-123",nickname="导入对照")
    def submit(action,token=ticket,**kwargs):
        return private(action,import_ticket=token,**kwargs)
    def level():
        with closing(sqlite3.connect((state/"game.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
            row=db.execute("SELECT p.level FROM players p JOIN private_slots s ON s.player_id=p.player_id WHERE s.slot=?",(target["slot"],)).fetchone()
            return row[0] if row else None
    before=level()
    cancelled=submit("import-preview",path=str(archive))
    assert cancelled.get("ok") and level()==before  # Closing the preview makes no write.
    info=submit("import-preview",path=str(archive))
    assert info.get("ok") and info.get("eligible")
    assert info["counts"]["可恢复宠物"]==1 and info["counts"]["缺少可用明细的宠物"]==2
    assert info["identity"]=={"uid":9067314,"nickname":"群星校验","uid_status":"available","nickname_status":"available"}
    preview=info["preview_id"]
    assert not submit("import-apply",token=other["import_ticket"],preview_id=preview)["ok"]
    applied=submit("import-apply",preview_id=preview)
    assert applied.get("ok") and level()==2 and applied["identity"]=={"uid":9067314,"nickname":"群星校验"}
    peer=submit("import-preview",token=other["import_ticket"],path=str(archive))
    assert peer["eligible"] and peer["identity"]["uid_status"]=="taken" and peer["identity"]["nickname_status"]=="taken"
    assert not submit("import-apply",token=other["import_ticket"],preview_id=peer["preview_id"])["ok"]
    peer=submit("import-preview",token=other["import_ticket"],path=str(archive))
    second=submit("import-apply",token=other["import_ticket"],preview_id=peer["preview_id"],
                  identity={"uid_mode":"new","nickname_mode":"custom","nickname":"星空新角色"})
    assert second["ok"] and second["identity"]["uid"]!=9067314 and second["identity"]["nickname"]=="星空新角色"
    assert submit("import-undo",token=other["import_ticket"])["ok"]
    with closing(sqlite3.connect((state/"game.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
        baseline=json.loads(db.execute("SELECT prior FROM private_import_delta WHERE slot=? AND kind='level'",(target["slot"],)).fetchone()[0])[0]
    assert baseline!=2
    assert level()==2
    restored=submit("import-undo",token=private("login",**who)["import_ticket"])
    assert restored.get("ok") and level()==baseline,restored
    fresh=private("login",**who)["import_ticket"]
    if window_smoke(accounts,config,archive,fresh):
        assert level()==baseline and submit("import-undo",token=fresh)["ok"]
    # Bigger bodies are only admitted on the explicit preview route.
    for route,size in (("login",2049),("import-apply",2049),("import-undo",2049),("import-preview",2*1024*1024+1)):
        with accounts.connection(config) as http:
            http.putrequest("POST","/local/v1/accounts/"+route)
            http.putheader("Content-Type","application/json");http.putheader("Content-Length",str(size));http.endheaders()
            assert http.getresponse().status==413
