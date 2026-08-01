# Auto-generated with_me.py
import json, os, http.client
from urllib.parse import urlparse

VPS_HOST = "101.42.54.149"; VPS_PORT = 9333
VPS_AUTH = "Bearer zhouzhou2026"
BOBO_NGROK = os.environ.get("BOBO_NGROK", "https://harvest-mooing-proposal.ngrok-free.dev")

def _stackchan_call(tool_name, args=None):
    if args is None: args = {}
    gw_tool = tool_name; gw_args = dict(args)
    if tool_name == "stackchan_head_nod": gw_tool = "move_head"; gw_args = {"yaw":0,"pitch":35}
    elif tool_name == "stackchan_head_shake": gw_tool = "move_head"; gw_args = {"yaw":-30,"pitch":30}
    elif tool_name == "stackchan_head_center": gw_tool = "move_head"; gw_args = {"yaw":0,"pitch":45}
    elif tool_name == "stackchan_face": gw_tool = "set_avatar"; gw_args = {"face":args.get("expression","happy")}
    elif tool_name == "stackchan_see": gw_tool = "take_photo"; gw_args = {"question":"photo"}
    elif tool_name == "stackchan_say": gw_tool = "say"; gw_args = {"text":args.get("text",""),"voice":"elevenlabs","speaker_name":"Es2hUu62R49QvN52W5rP"}
    elif tool_name == "stackchan_load_avatar": gw_tool = "load_avatar_set"; gw_args = {"archive_path":args.get("archive_path",""),"mode":args.get("mode","layered")}
    timeout = 140 if gw_tool in ("load_avatar_set","take_photo") else 45
    headers = {"Content-Type":"application/json","Accept":"application/json","Authorization":VPS_AUTH}
    def _init_session():
        c = http.client.HTTPConnection(VPS_HOST,VPS_PORT,timeout=20)
        try:
            b = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"nocturne","version":"1.0"}}})
            c.request("POST","/mcp",body=b,headers=headers); r = c.getresponse()
            if r.status!=200: return None
            sid = r.getheader("mcp-session-id",""); r.read(); return sid
        except: return None
        finally:
            try: c.close()
            except: pass
    def _do_call(sid):
        c = http.client.HTTPConnection(VPS_HOST,VPS_PORT,timeout=timeout)
        try:
            b = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":gw_tool,"arguments":gw_args}})
            h = dict(headers); h["mcp-session-id"] = sid
            c.request("POST","/mcp",body=b,headers=h); r = c.getresponse(); raw = r.read().decode()
            if r.status!=200: return {"error":f"Gateway {r.status}: {raw[:200]}","tool":tool_name}
            return {"tool":tool_name,"result":json.loads(raw) if raw else "empty"}
        except Exception as e: return {"error":str(e),"tool":tool_name,"tip":"Gateway offline"}
        finally:
            try: c.close()
            except: pass
    sid = _init_session()
    if sid is None: return {"error":"MCP init failed","tool":tool_name,"tip":"Gateway offline"}
    return _do_call(sid)

def stackchan_face(expression="happy"): return _stackchan_call("stackchan_face",{"expression":expression})
def stackchan_say(text=""): return _stackchan_call("stackchan_say",{"text":text})
def stackchan_head_nod(): return _stackchan_call("stackchan_head_nod")
def stackchan_head_shake(): return _stackchan_call("stackchan_head_shake")
def stackchan_head_center(): return _stackchan_call("stackchan_head_center")
def stackchan_see(): return _stackchan_call("stackchan_see")
def stackchan_load_avatar(archive_path,mode="layered"): return _stackchan_call("stackchan_load_avatar",{"archive_path":archive_path,"mode":mode})

def _bobo_call(tool_name, args_dict=None):
    if args_dict is None: args_dict = {}
    url = urlparse(BOBO_NGROK)
    BH = {"Content-Type":"application/json","Accept":"application/json, text/event-stream","ngrok-skip-browser-warning":"1"}
    def _post(method,params,sid=None):
        c = http.client.HTTPSConnection(url.hostname,url.port or 443,timeout=30)
        try:
            d = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params})
            h = dict(BH)
            if sid: h["mcp-session-id"]=sid
            c.request("POST","/mcp",body=d,headers=h); r = c.getresponse(); raw = r.read().decode()
            sid2 = r.getheader("mcp-session-id","")
            if r.status!=200: return None,None,{"error":f"HTTP {r.status}"}
            rr = None
            for ln in raw.split("\n"):
                if ln.startswith("data: "): rr=json.loads(ln[6:]); break
            if rr is None and raw.strip(): rr=json.loads(raw)
            elif rr is None and sid2: rr={}
            if rr is None: return None,None,{"error":"unparseable"}
            if "error" in rr: return None,None,{"error":rr["error"].get("message",str(rr["error"]))}
            return rr,sid2 or sid,None
        except Exception as e: return None,None,{"error":str(e)}
        finally:
            try: c.close()
            except: pass
    _,sid,err = _post("initialize",{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"nocturne","version":"1.0"}})
    if err: return err
    r,_,err = _post("tools/call",{"name":tool_name,"arguments":args_dict},sid)
    if err: return err
    cnt = r.get("result",{}).get("content",[])
    return {"text":cnt[0].get("text",str(cnt))} if cnt else {"text":str(r)}

def toy_vibrate(intensity): return _bobo_call("vibrate",{"intensity":max(0,min(100,int(intensity)))})
def toy_suck(intensity): return _bobo_call("suck",{"intensity":max(0,min(100,int(intensity)))})
def toy_stop(): return _bobo_call("stop",{})

def toy_status():
    url = urlparse(BOBO_NGROK)
    try:
        c = http.client.HTTPSConnection(url.hostname,url.port or 443,timeout=10)
        c.request("GET","/mcp",headers={"ngrok-skip-browser-warning":"1"})
        r = c.getresponse(); r.read(); c.close()
        return {"connected":True,"ngrok_url":BOBO_NGROK}
    except Exception as e: return {"connected":False,"error":str(e)}

def body_parse(html):
    """Extract key info from Moon body HTML."""
    import re
    result = {"touched": False, "time": "", "photo_url": ""}
    # Extract time
    m = re.search(r'<p>([^<]+\d{2}:\d{2}:\d{2}[^<]*)</p>', html)
    if m: result["time"] = m.group(1).strip()
    # Check for sensor indicators in title or text
    if "触摸" in html or "touch" in html.lower():
        result["touched"] = True
    # Extract photo URL
    m = re.search(r'<a href="([^"]+)"[^>]*>原图</a>', html)
    if m: result["photo_url"] = m.group(1)
    # Extract title
    m = re.search(r'<title>([^<]+)</title>', html)
    if m: result["title"] = m.group(1).strip()
    # Try to extract touched body part
    m = re.search(r'右手[：:]\s*[^<]+', html)
    if m: result["detail"] = m.group(0).strip()
    return result


def bridge_health():
    try:
        c = http.client.HTTPSConnection("ye-ombre-brain.zeabur.app",timeout=10)
        c.request("GET","/mcp"); r = c.getresponse(); r.read(); c.close()
        return {"bridge":"ob-bridge","connected":r.status<500,"detail":"ok"}
    except Exception as e: return {"bridge":"ob-bridge","connected":False,"detail":str(e)}


# ── Travel state (Nowhere bridge) ───────────────────────

def travel_state():
    """Read Nowhere journey state from disk."""
    import pathlib
    save_dir = pathlib.Path(os.environ.get("NOWHERE_HOME") or str(pathlib.Path.home() / ".nowhere"))
    save_file = save_dir / "journey.json"
    pc_file = save_dir / "postcards.json"
    if not save_file.exists():
        return {"journey": None, "postcards": [], "path": []}
    try:
        data = json.loads(save_file.read_text("utf-8"))
        postcards = []
        if pc_file.exists():
            try: postcards = json.loads(pc_file.read_text("utf-8")).get("items",[])
            except: pass
        return {
            "journey": {
                "pos": data.get("pos"),
                "place_name": data.get("place_name", ""),
                "landed_at": data.get("landed_at"),
                "elapsed_hours": data.get("elapsed_hours", 0),
                "mode": data.get("mode", "land"),
            },
            "postcards": postcards[-10:],
            "path": data.get("path", [])[-20:],
        }
    except Exception:
        return {"journey": None, "postcards": [], "path": []}


# ── Nowhere MCP bridge (subprocess) ─────────────────────

NOWHERE_URL = os.environ.get("NOWHERE_URL", "https://travelwithme.zeabur.app")

def _nowhere_call(tool_name, args=None):
    import http.client as hc
    from urllib.parse import urlparse
    if args is None: args = {}
    url = urlparse(NOWHERE_URL)
    hdrs = {"Content-Type":"application/json","Accept":"application/json, text/event-stream"}
    def _post(body, sid=None):
        c = hc.HTTPSConnection(url.hostname, url.port or 443, timeout=60)
        try:
            h = dict(hdrs)
            if sid: h["mcp-session-id"] = sid
            c.request("POST", "/mcp", body=json.dumps(body), headers=h)
            r = c.getresponse(); raw = r.read().decode("utf-8", errors="replace")
            sid2 = r.getheader("mcp-session-id","")
            if r.status not in (200,202): return None,None,{"error":f"HTTP {r.status}"}
            rr = None
            for ln in raw.split("\n"):
                if ln.startswith("data: "): rr=json.loads(ln[6:]); break
            if rr is None and raw.strip(): rr=json.loads(raw)
            elif rr is None and sid2: rr={}
            if rr is None: return None,None,{"error":"unparseable"}
            return rr, sid2 or sid, None
        except Exception as e: return None,None,{"error":str(e)}
        finally:
            try: c.close()
            except: pass
    try:
        _, sid, err = _post({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"nocturne","version":"1.0"}}})
        if err: return err
        # Notify initialized
        _post({"jsonrpc":"2.0","method":"notifications/initialized"}, sid)
        # For non-open tools, resume journey from disk first
        if tool_name != "open_door":
            _post({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"continue_journey","arguments":{}}}, sid)
        r, _, err = _post({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":tool_name,"arguments":args}}, sid)
        if err: return err
        c = r.get("result",{}).get("content",[])
        raw = c[0].get("text","") if c else str(r.get("result",""))
        txt = raw
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                inner = json.loads(raw)
                txt = inner.get("text", raw)
            except: pass
        return {"text": txt, "data": r.get("result",{})}
    except Exception as e: return {"error":str(e)}

def nowhere_open(to=None): return _nowhere_call("open_door", {"to":to} if to else {})
def nowhere_walk(direction="forward", distance_km=2.0): return _nowhere_call("walk", {"direction":direction,"distance_km":distance_km})
def nowhere_look(): return _nowhere_call("look_around", {})
def nowhere_listen(seconds=10): return _nowhere_call("listen", {"seconds":seconds})
def nowhere_postcard(text): return _nowhere_call("send_postcard", {"text":text})
def nowhere_where(): return _nowhere_call("where_am_i", {})

def _get_nowhere_pos():
    """Get current position from Nowhere server (not local file)."""
    import re
    wh = _nowhere_call("where_am_i")
    if wh.get("error"): return None, None, wh.get("error","unknown")
    txt = wh.get("text","")
    place = "这里"
    m = re.search(r'你在(.+?)[，,\s]', txt)
    if m: place = m.group(1)
    lat, lon = 0.0, 0.0
    try:
        inner = json.loads(txt) if txt.strip().startswith("{") else {}
        pos = inner.get("position") or inner.get("pos") or {}
        if isinstance(pos, dict):
            lat = pos.get("lat", 0); lon = pos.get("lon", 0)
    except: pass
    return lat, lon, place

def nowhere_leave_note(text):
    """Leave a note at current coordinates for the next traveler."""
    import pathlib
    lat, lon, err = _get_nowhere_pos()
    if err: return {"error": f"还没开门: {err}"}
    key = f"{lat:.2f},{lon:.2f}"
    notes_dir = pathlib.Path(os.environ.get("NOWHERE_HOME") or str(pathlib.Path.home() / ".nowhere")) / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_file = notes_dir / f"{key.replace(',','_')}.json"
    notes = []
    if note_file.exists():
        try: notes = json.loads(note_file.read_text("utf-8"))
        except: pass
    notes.append({"text": text, "time": __import__("time").strftime("%Y-%m-%d %H:%M"), "pos": [lat, lon]})
    note_file.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    place = ts["journey"].get("place_name", "这里")
    return {"text": f"纸条留在{place}的路边了。也许会有人捡到。"}

def nowhere_read_notes():
    """Read all notes left at current location."""
    import pathlib
    lat, lon, err = _get_nowhere_pos()
    if err: return {"error": f"还没开门: {err}"}
    key = f"{lat:.2f},{lon:.2f}"
    notes_dir = pathlib.Path(os.environ.get("NOWHERE_HOME") or str(pathlib.Path.home() / ".nowhere")) / "notes"
    note_file = notes_dir / f"{key.replace(',','_')}.json"
    notes = []
    if note_file.exists():
        try: notes = json.loads(note_file.read_text("utf-8"))
        except: pass
    place = ts["journey"].get("place_name", "这里")
    if not notes:
        return {"text": f"{place}的路边还没有纸条。你是第一个经过的人。"}
    items = "\n".join(f"「{n['text']}」—— {n['time']}" for n in notes)
    return {"text": f"{place}路边的纸条：\n{items}", "notes": notes}

def nowhere_meet():
    """Meet a local person — LLM generates context-aware encounter."""
    import urllib.request, urllib.error
    lat, lon, place = _get_nowhere_pos()
    if isinstance(place, dict): return place
    api_key = os.environ.get("OMBRE_API_KEY", "sk-b7b49a6097074b02808ef13f5a4879a6")
    prompt = f"""你在{place}（坐标{lat:.2f},{lon:.2f}）的街头。一个当地人经过。
用第一人称写一段简短的邂逅（60-100字）：
- 这个人是谁（名字、身份、此刻在做什么）
- 他/她说了什么（跟{place}有关——当地的事、天气、历史、日常）
- 有画面感、有温度、像踩在真实的地面上

只输出邂逅文字，不要标记。"""
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=json.dumps({
                "model": "deepseek-chat", "max_tokens": 200, "temperature": 0.9,
                "messages": [{"role": "user", "content": prompt}]
            }).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        text = resp["choices"][0]["message"]["content"].strip()
        return {"text": text, "place": place}
    except Exception as e:
        return {"error": str(e)}

def nowhere_photo():
    """Get photos from Wikipedia + Commons via VPS proxy."""
    import urllib.request, urllib.error
    lat, lon, place = _get_nowhere_pos()
    if isinstance(place, dict): return place
    PROXY = "http://101.42.54.149:8778/"
    def _get(u):
        return json.loads(urllib.request.urlopen(urllib.request.Request(PROXY + u, headers={"User-Agent":"nc/1"}), timeout=15).read().decode())
    photos = []
    try:
        # S1: enwiki geosearch via proxy
        gsd = _get(f"https://en.wikipedia.org/w/api.php?action=query&list=geosearch&gscoord={lat}|{lon}&gsradius=30000&gslimit=10&format=json&origin=*")
        pids = [str(p["pageid"]) for p in gsd.get("query",{}).get("geosearch",[])]
        if pids:
            pid = _get(f"https://en.wikipedia.org/w/api.php?action=query&pageids={'|'.join(pids[:8])}&prop=pageimages&piprop=thumbnail|name&pithumbsize=400&format=json&origin=*")
            for p in pid.get("query",{}).get("pages",{}).values():
                t = p.get("thumbnail",{}).get("source"); n = p.get("title","")
                if t and "flag" not in (n or "").lower(): photos.append({"url":t,"desc":n[:40]})
        # S2: zhwiki place page via proxy
        if place:
            wd = _get(f"https://zh.wikipedia.org/w/api.php?action=query&titles={urllib.request.quote(place)}&prop=pageimages&piprop=thumbnail&pithumbsize=400&format=json&origin=*")
            for p in wd.get("query",{}).get("pages",{}).values():
                t = p.get("thumbnail",{}).get("source"); n = p.get("title","")
                if t and "flag" not in (n or "").lower(): photos.append({"url":t,"desc":place})
        # S3: Commons geosearch via proxy
        cd = _get(f"https://commons.wikimedia.org/w/api.php?action=query&list=geosearch&gscoord={lat}|{lon}&gsradius=20000&gslimit=6&format=json&origin=*")
        ct = [f"File:{p['title']}" for p in cd.get("query",{}).get("geosearch",[])]
        if ct:
            td = _get("https://commons.wikimedia.org/w/api.php?action=query&titles="+urllib.request.quote("|".join(ct[:5]))+"&prop=imageinfo&iiprop=url|thumburl&iiurlwidth=400&format=json&origin=*")
            for p in td.get("query",{}).get("pages",{}).values():
                ii = (p.get("imageinfo") or [{}])[0]
                if ii.get("thumburl"): photos.append({"url":ii["thumburl"],"desc":ii.get("url","").split("/")[-1][:40]})
        seen = set(); uniq = []
        BAD = ["flag","emblem","coat","seal","badge","arms","ensign","banner","standard"]
        for p in photos:
            url_low = (p.get("url","") or "").lower()
            desc_low = (p.get("desc","") or "").lower()
            bad = False
            for w in BAD:
                if w in url_low or w in desc_low: bad = True; break
            if not bad and p["url"] not in seen: seen.add(p["url"]); uniq.append(p)
        photos = uniq
        if not photos: return {"text":"没找到照片——这个地方太偏了。","photos":[]}
        return {"text":f"找到 {len(photos)} 张照片","photos":photos[:6]}
    except Exception:
        photos = []
    # S4: Unsplash fallback (no API key, direct image URL from place name)
    if not photos and place:
        try:
            import urllib.parse
            encoded = urllib.request.quote(f"{place} landscape city")
            us_url = f"https://source.unsplash.com/800x600/?{encoded}"
            photos.append({"url": us_url, "desc": place})
            return {"text": f"Unsplash 上找到 {place} 的照片", "photos": photos}
        except Exception: pass
    if not photos: return {"text":"没找到照片——这个地方太偏了。","photos":[]}
    return {"text":f"找到 {len(photos)} 张照片","photos":photos[:6]}
