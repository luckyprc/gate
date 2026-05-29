#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
import base64
import csv
import io
import json
import os
import re
from datetime import datetime, timezone

VPNGATE_API = "http://www.vpngate.net/api/iphone/"
OUTPUT_DIR = "dist"
TOP_N = 20
MIN_SPEED = 500000      # 最小速度 500 KB/s
MAX_PING = 300          # 最大延迟 300ms
PREFERRED_COUNTRIES = []  # 留空表示不限制，如 ["JP","KR","US","DE"]

def fetch_csv():
    r = requests.get(VPNGATE_API, timeout=30)
    r.raise_for_status()
    return r.text

def parse_csv(text):
    lines = text.strip().splitlines()
    # 找到 *vpn_servers 标记行
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("*vpn_servers"):
            start = i
            break
    # 去掉标记行，保留标题+数据
    csv_body = "\n".join(lines[start+1:])
    reader = csv.reader(io.StringIO(csv_body))
    rows = list(reader)
    # 第一行是标题
    if rows and rows[0][0].lower() == "#hostname":
        rows = rows[1:]
    return rows

def rows_to_nodes(rows):
    nodes = []
    for row in rows:
        if len(row) < 15:
            continue
        try:
            speed = int(row[4]) if row[4] else 0
            ping = int(row[3]) if row[3] else 9999
            nodes.append({
                "hostname": row[0],
                "ip": row[1],
                "score": int(row[2]) if row[2] else 0,
                "ping": ping,
                "speed": speed,
                "country_long": row[5],
                "country_short": row[6],
                "num_sessions": row[7],
                "uptime": row[8],
                "total_users": row[9],
                "total_traffic": row[10],
                "log_type": row[11],
                "operator": row[12],
                "message": row[13],
                "ovpn_b64": row[14].strip()
            })
        except Exception:
            continue
    return nodes

def filter_nodes(nodes):
    filtered = []
    for n in nodes:
        if n["speed"] < MIN_SPEED:
            continue
        if n["ping"] > MAX_PING:
            continue
        if PREFERRED_COUNTRIES and n["country_short"].upper() not in PREFERRED_COUNTRIES:
            continue
        filtered.append(n)
    # 排序：速度降序，延迟升序
    filtered.sort(key=lambda x: (-x["speed"], x["ping"]))
    return filtered[:TOP_N]

def extract_remote_port(ovpn_text):
    """从 oVPN 配置中提取 remote 和 port"""
    remote = None
    port = 1194
    for line in ovpn_text.splitlines():
        if line.startswith("remote "):
            parts = line.split()
            if len(parts) >= 2:
                remote = parts[1]
            if len(parts) >= 3:
                try:
                    port = int(parts[2])
                except ValueError:
                    pass
            break
    return remote, port

def generate(nodes):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    updated = datetime.now(timezone.utc).isoformat()

    # 1. nodes.json — 完整元数据
    with open(f"{OUTPUT_DIR}/nodes.json", "w", encoding="utf-8") as f:
        json.dump({
            "source": "vpngate.net",
            "updated": updated,
            "count": len(nodes),
            "nodes": nodes
        }, f, ensure_ascii=False, indent=2)

    # 2. sub.txt — 标准 Base64 订阅（每行一个 base64(oVPN配置)，整体再 base64）
    ovpn_lines = [n["ovpn_b64"] for n in nodes]
    inner = "\n".join(ovpn_lines)
    sub_b64 = base64.b64encode(inner.encode()).decode()
    with open(f"{OUTPUT_DIR}/sub.txt", "w") as f:
        f.write(sub_b64)

    # 3. singbox.json — SingBox 出站配置
    outbounds = []
    for i, n in enumerate(nodes):
        ovpn_text = base64.b64decode(n["ovpn_b64"]).decode("utf-8", errors="ignore")
        remote, port = extract_remote_port(ovpn_text)
        server = remote or n["ip"]
        outbounds.append({
            "type": "openvpn",
            "tag": f"{n['country_short']}-{i+1}-{n['hostname'][:8]}",
            "server": server,
            "server_port": port,
            "ovpn_config": n["ovpn_b64"]
        })
    singbox = {
        "log": {"level": "warn"},
        "outbounds": outbounds + [{"type": "direct", "tag": "direct"}]
    }
    with open(f"{OUTPUT_DIR}/singbox.json", "w", encoding="utf-8") as f:
        json.dump(singbox, f, ensure_ascii=False, indent=2)

    # 4. v2ray.json — 节点信息（非标准订阅，仅作数据展示）
    v2ray_like = []
    for n in nodes:
        v2ray_like.append({
            "name": f"{n['country_short']}-{n['hostname']}",
            "type": "openvpn",
            "server": n["ip"],
            "port": 1194,
            "ovpn_config": n["ovpn_b64"]
        })
    with open(f"{OUTPUT_DIR}/v2ray.json", "w", encoding="utf-8") as f:
        json.dump(v2ray_like, f, ensure_ascii=False, indent=2)

    # 5. index.html
    rows_html = "".join([
        f"<tr><td>{n['country_short']}</td><td>{n['ip']}</td>"
        f"<td>{n['speed']}</td><td>{n['ping']}</td></tr>"
        for n in nodes
    ])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>VPN Gate 订阅</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px}}
table{{border-collapse:collapse;width:100%;margin-top:20px}}
th,td{{border:1px solid #ddd;padding:8px;text-align:left}}
th{{background:#f5f5f5}}
code{{background:#f0f0f0;padding:2px 6px;border-radius:4px}}
</style>
</head>
<body>
<h1>VPN Gate 节点订阅</h1>
<p>更新时间：<b>{updated}</b> | 节点数：<b>{len(nodes)}</b></p>
<p>
  <a href="sub.txt"><code>sub.txt</code></a>（Base64 订阅） |
  <a href="nodes.json"><code>nodes.json</code></a> |
  <a href="singbox.json"><code>singbox.json</code></a> |
  <a href="v2ray.json"><code>v2ray.json</code></a>
</p>
<table>
<tr><th>国家</th><th>IP</th><th>速度(Byte/s)</th><th>延迟(ms)</th></tr>
{rows_html}
</table>
</body>
</html>"""
    with open(f"{OUTPUT_DIR}/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[OK] 生成 {len(nodes)} 个节点，输出到 {OUTPUT_DIR}/")

def main():
    print("[*] 正在抓取 VPN Gate API...")
    csv_text = fetch_csv()
    rows = parse_csv(csv_text)
    nodes = rows_to_nodes(rows)
    print(f"[*] 原始节点总数: {len(nodes)}")
    top = filter_nodes(nodes)
    print(f"[*] 筛选后节点: {len(top)}")
    generate(top)

if __name__ == "__main__":
    main()
