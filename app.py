#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币多周期趋势监控 —— 网页版后端 (Flask)
部署到服务器后，任何设备打开浏览器访问 http://服务器IP:5000 即可使用。
"""

import os
import threading
import time
import traceback

from flask import Flask, jsonify, request, render_template

import crypto_core as core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

app = Flask(__name__)

_lock = threading.Lock()
_state = {
    "cfg": core.load_config(CONFIG_PATH),
    "holdings_result": [],
    "holdings_updated_at": None,
    "holdings_status": "idle",       # idle / running / error
    "holdings_error": None,
    "recommend_result": [],
    "recommend_updated_at": None,
    "recommend_status": "idle",      # idle / running / done / error
    "recommend_error": None,
    "recommend_log": "",
}


# ---------------------------------------------------------------------------
# 后台任务
# ---------------------------------------------------------------------------
def _refresh_holdings_worker():
    with _lock:
        if _state["holdings_status"] == "running":
            return
        _state["holdings_status"] = "running"
        cfg = dict(_state["cfg"])
    try:
        symbols = list(cfg.get("holdings", []))
        results = core.refresh_holdings_data(cfg, symbols) if symbols else []
        serialized = [core.serialize_result(r) for r in results]
        with _lock:
            _state["holdings_result"] = serialized
            _state["holdings_updated_at"] = time.time()
            _state["holdings_status"] = "idle"
            _state["holdings_error"] = None
    except Exception as e:
        with _lock:
            _state["holdings_status"] = "error"
            _state["holdings_error"] = str(e)


def _scan_recommend_worker():
    with _lock:
        if _state["recommend_status"] == "running":
            return
        _state["recommend_status"] = "running"
        _state["recommend_log"] = "启动中..."
        cfg = dict(_state["cfg"])
    try:
        session = core.get_session(cfg)

        def log(msg):
            with _lock:
                _state["recommend_log"] = msg

        top_n = int(cfg.get("recommend_count", 10))
        results = core.pick_recommend_symbols(session, top_n=top_n, log=log)
        serialized = [core.serialize_result(r) for r in results]
        with _lock:
            _state["recommend_result"] = serialized
            _state["recommend_updated_at"] = time.time()
            _state["recommend_status"] = "done"
            _state["recommend_error"] = None
            _state["recommend_log"] = "完成"
    except Exception as e:
        with _lock:
            _state["recommend_status"] = "error"
            _state["recommend_error"] = str(e)
            _state["recommend_log"] = f"出错: {e}"
        traceback.print_exc()


def _auto_refresh_loop():
    """后台常驻线程：按配置的间隔自动刷新持仓"""
    while True:
        with _lock:
            minutes = int(_state["cfg"].get("auto_refresh_minutes", 5) or 0)
        if minutes > 0:
            _refresh_holdings_worker()
            time.sleep(minutes * 60)
        else:
            time.sleep(10)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API - 配置 & 持仓管理
# ---------------------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def get_config():
    with _lock:
        return jsonify(_state["cfg"])


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.get_json(force=True) or {}
    with _lock:
        cfg = _state["cfg"]
        if "proxy" in data:
            cfg["proxy"] = str(data["proxy"] or "").strip()
        if "auto_refresh_minutes" in data:
            try:
                cfg["auto_refresh_minutes"] = int(data["auto_refresh_minutes"])
            except (TypeError, ValueError):
                pass
        if "recommend_count" in data:
            try:
                cfg["recommend_count"] = max(1, min(30, int(data["recommend_count"])))
            except (TypeError, ValueError):
                pass
        core.save_config(CONFIG_PATH, cfg)
        return jsonify(cfg)


@app.route("/api/holdings/add", methods=["POST"])
def add_holding():
    data = request.get_json(force=True) or {}
    raw = str(data.get("symbol", "")).strip().upper()
    if not raw:
        return jsonify({"ok": False, "error": "币种不能为空"}), 400
    symbol = raw if raw.endswith("USDT") else raw + "USDT"
    with _lock:
        holdings = _state["cfg"]["holdings"]
        if symbol not in holdings:
            holdings.append(symbol)
            core.save_config(CONFIG_PATH, _state["cfg"])
    threading.Thread(target=_refresh_holdings_worker, daemon=True).start()
    return jsonify({"ok": True, "symbol": symbol})


@app.route("/api/holdings/remove", methods=["POST"])
def remove_holding():
    data = request.get_json(force=True) or {}
    symbol = str(data.get("symbol", "")).strip().upper()
    with _lock:
        holdings = _state["cfg"]["holdings"]
        if symbol in holdings:
            holdings.remove(symbol)
            core.save_config(CONFIG_PATH, _state["cfg"])
        _state["holdings_result"] = [r for r in _state["holdings_result"] if r["symbol"] != symbol]
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API - 持仓行情
# ---------------------------------------------------------------------------
@app.route("/api/holdings", methods=["GET"])
def get_holdings():
    with _lock:
        status = _state["holdings_status"]
    if status != "running":
        threading.Thread(target=_refresh_holdings_worker, daemon=True).start()
    with _lock:
        return jsonify({
            "status": _state["holdings_status"],
            "updated_at": _state["holdings_updated_at"],
            "error": _state["holdings_error"],
            "result": _state["holdings_result"],
        })


@app.route("/api/holdings/refresh", methods=["POST"])
def force_refresh_holdings():
    threading.Thread(target=_refresh_holdings_worker, daemon=True).start()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API - 强势推荐
# ---------------------------------------------------------------------------
@app.route("/api/recommend/scan", methods=["POST"])
def scan_recommend():
    with _lock:
        if _state["recommend_status"] == "running":
            return jsonify({"ok": False, "error": "已有扫描任务在进行"}), 409
    threading.Thread(target=_scan_recommend_worker, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/recommend/status", methods=["GET"])
def recommend_status():
    with _lock:
        return jsonify({
            "status": _state["recommend_status"],
            "updated_at": _state["recommend_updated_at"],
            "error": _state["recommend_error"],
            "log": _state["recommend_log"],
            "result": _state["recommend_result"],
        })


if __name__ == "__main__":
    threading.Thread(target=_auto_refresh_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
