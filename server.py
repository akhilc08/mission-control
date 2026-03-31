#!/usr/bin/env python3
"""OpenClaw Mission Control — stdlib-only HTTP server on port 4242."""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

STATE_PATH = Path(__file__).parent / "state.json"
lock = threading.Lock()
sse_clients: list = []  # list of (wfile, lock_per_client)


def read_state() -> dict:
    with lock:
        return json.loads(STATE_PATH.read_text())


def write_state(state: dict):
    with lock:
        STATE_PATH.write_text(json.dumps(state, indent=2))


def deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def notify_sse():
    dead = []
    for i, (wfile, cl) in enumerate(sse_clients):
        try:
            with cl:
                wfile.write(b"data: update\n\n")
                wfile.flush()
        except Exception:
            dead.append(i)
    for i in reversed(dead):
        sse_clients.pop(i)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mission Control — OpenClaw</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0a0f;--surface:#12121a;--surface2:#1a1a2e;--border:#1e1e2e;
  --accent:#7c3aed;--green:#22c55e;--yellow:#eab308;--red:#ef4444;
  --orange:#f97316;--teal:#14b8a6;--text:#e2e8f0;--muted:#64748b;
  --sidebar-w:220px;
}
body{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;font-size:13px;min-height:100vh;display:flex;overflow:hidden}

/* Sidebar */
.sidebar{width:var(--sidebar-w);background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;height:100vh;position:fixed;left:0;top:0;z-index:100}
.sidebar-brand{padding:20px 16px 16px;font-size:11px;font-weight:700;letter-spacing:3px;color:var(--accent);text-transform:uppercase;border-bottom:1px solid var(--border);text-shadow:0 0 20px rgba(124,58,237,.4)}
.sidebar-brand small{display:block;font-size:9px;color:var(--muted);letter-spacing:1px;margin-top:4px;font-weight:500}
.sidebar-nav{flex:1;padding:8px 0;overflow-y:auto}
.nav-item{display:flex;align-items:center;gap:12px;padding:10px 16px;cursor:pointer;color:var(--muted);font-size:12px;font-weight:500;transition:all .15s;border-left:3px solid transparent;user-select:none}
.nav-item:hover{background:rgba(124,58,237,.06);color:var(--text)}
.nav-item.active{color:var(--accent);background:rgba(124,58,237,.1);border-left-color:var(--accent)}
.nav-icon{width:18px;height:18px;flex-shrink:0;opacity:.7}
.nav-item.active .nav-icon{opacity:1}
.sidebar-footer{padding:12px 16px;border-top:1px solid var(--border);font-size:10px;color:var(--muted)}
.sidebar-footer .status-row{display:flex;align-items:center;gap:8px}
.status-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.status-dot.working{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 1.5s infinite}
.status-dot.idle{background:var(--muted)}
.status-dot.error{background:var(--red);box-shadow:0 0 8px var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* Main */
.main{margin-left:var(--sidebar-w);flex:1;height:100vh;overflow-y:auto;padding:24px}
.main-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.main-title{font-size:20px;font-weight:700;letter-spacing:1px}
.clock{font-size:12px;color:var(--muted)}

/* Panels */
.panel-view{display:none}
.panel-view.active{display:block}

/* Shared */
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.badge-green{background:rgba(34,197,94,.15);color:var(--green)}
.badge-yellow{background:rgba(234,179,8,.15);color:var(--yellow)}
.badge-red{background:rgba(239,68,68,.15);color:var(--red)}
.badge-orange{background:rgba(249,115,22,.15);color:var(--orange)}
.badge-teal{background:rgba(20,184,166,.15);color:var(--teal)}
.badge-muted{background:rgba(100,116,139,.15);color:var(--muted)}
.badge-purple{background:rgba(124,58,237,.15);color:var(--accent)}
.empty-state{color:var(--muted);font-style:italic;padding:40px;text-align:center;font-size:13px}
.progress-bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.progress-fill{height:100%;background:var(--accent);border-radius:3px;transition:width .3s}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:8px 10px;color:var(--muted);border-bottom:1px solid var(--border);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
td{padding:8px 10px;border-bottom:1px solid rgba(30,30,46,.5)}
tr:hover td{background:rgba(124,58,237,.04)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}

/* ===== TASKS BOARD ===== */
.kanban-progress{margin-bottom:16px}
.kanban-progress-label{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:6px}
.kanban-progress .progress-bar{height:8px}
.kanban{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;min-height:400px}
.kanban-col{background:var(--surface);border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column;min-height:300px}
.kanban-col-header{padding:12px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
.kanban-col-header .count{background:var(--surface2);padding:2px 8px;border-radius:10px;font-size:10px;color:var(--muted)}
.kanban-col-body{padding:8px;flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.task-card{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px 12px;cursor:default;transition:border-color .15s}
.task-card:hover{border-color:var(--accent)}
.task-card-title{font-size:12px;font-weight:600;margin-bottom:6px}
.task-card-meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.task-card-project{font-size:10px;color:var(--muted);background:var(--surface2);padding:1px 6px;border-radius:3px}
.pri-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.pri-critical{background:var(--red)}
.pri-high{background:var(--orange)}
.pri-medium{background:var(--yellow)}
.pri-low{background:var(--muted)}
.kanban-empty{color:var(--muted);font-size:11px;font-style:italic;text-align:center;padding:20px 8px;opacity:.6}

/* ===== CALENDAR ===== */
.cal-section{margin-bottom:20px}
.cal-section-title{font-size:13px;font-weight:700;margin-bottom:10px;color:var(--accent);text-transform:uppercase;letter-spacing:1px}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
.cal-day-header{font-size:10px;color:var(--muted);text-align:center;padding:6px;text-transform:uppercase;font-weight:600}
.cal-day{background:var(--surface);border:1px solid var(--border);border-radius:4px;min-height:80px;padding:6px;font-size:10px}
.cal-day-num{color:var(--muted);margin-bottom:4px;font-weight:600}
.cal-block{padding:3px 5px;border-radius:3px;margin-bottom:2px;font-size:9px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cal-block.scraper{background:rgba(249,115,22,.2);color:var(--orange)}
.cal-block.watchlist{background:rgba(20,184,166,.2);color:var(--teal)}
.cal-block.other{background:rgba(124,58,237,.2);color:var(--accent)}
.cron-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.cron-card-left{display:flex;align-items:center;gap:12px}
.cron-status-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.cron-status-dot.ok{background:var(--green);box-shadow:0 0 6px var(--green)}
.cron-status-dot.fail{background:var(--red);box-shadow:0 0 6px var(--red)}
.cron-status-dot.unknown{background:var(--muted)}
.cron-info h4{font-size:13px;font-weight:600;margin-bottom:2px}
.cron-info span{font-size:11px;color:var(--muted)}
.cron-card-right{text-align:right;font-size:11px;color:var(--muted)}
.cron-countdown{font-size:13px;color:var(--text);font-weight:600}
.sparkline{display:inline-flex;gap:2px;align-items:center;margin-top:4px}
.spark-dot{width:6px;height:6px;border-radius:50%}
.spark-ok{background:var(--green)}
.spark-fail{background:var(--red)}
.spark-none{background:var(--muted);opacity:.3}

/* ===== MEMORY ===== */
.memory-layout{display:grid;grid-template-columns:200px 1fr;gap:16px;min-height:500px}
.memory-sidebar{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px}
.memory-sidebar h4{font-size:11px;color:var(--accent);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
.mem-cat{padding:6px 8px;font-size:11px;color:var(--muted);cursor:pointer;border-radius:4px;margin-bottom:2px}
.mem-cat:hover{background:var(--surface2);color:var(--text)}
.mem-cat.active{background:rgba(124,58,237,.15);color:var(--accent)}
.mem-cat .mem-count{float:right;font-size:10px;opacity:.6}
.memory-main{display:flex;flex-direction:column;gap:8px}
.memory-search{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:8px 12px;color:var(--text);font-family:inherit;font-size:12px;width:100%;outline:none}
.memory-search:focus{border-color:var(--accent)}
.log-entry{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:12px;cursor:pointer;transition:border-color .15s}
.log-entry:hover{border-color:var(--accent)}
.log-header{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.log-ts{font-size:11px;color:var(--muted)}
.log-msg{font-size:12px;line-height:1.5}
.log-expanded{display:none;margin-top:8px;padding-top:8px;border-top:1px solid var(--border);font-size:11px;color:var(--muted);white-space:pre-wrap}
.log-entry.open .log-expanded{display:block}
.log-entry.open{border-color:var(--accent)}

/* ===== OFFICE ===== */
.office-layout{display:grid;grid-template-columns:1fr 300px;gap:16px;min-height:500px}
.office-canvas{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;position:relative;min-height:400px}
.office-floor{width:100%;height:100%;position:absolute;top:0;left:0}
.office-sidebar{display:flex;flex-direction:column;gap:12px}
.office-panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px}
.office-panel h4{font-size:11px;color:var(--accent);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
.demo-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:6px 12px;border-radius:4px;font-size:11px;font-family:inherit;cursor:pointer;width:100%;text-align:left;margin-bottom:4px;transition:all .15s}
.demo-btn:hover{border-color:var(--accent);background:rgba(124,58,237,.1)}
.activity-mini .log-mini{font-size:10px;padding:4px 0;border-bottom:1px solid rgba(30,30,46,.3);color:var(--muted)}
.activity-mini .log-mini:last-child{border:none}
.pixel-font{font-family:'Press Start 2P',monospace}

/* ===== TEAM ===== */
.team-featured{background:linear-gradient(135deg,rgba(124,58,237,.1),rgba(20,184,166,.05));border:1px solid var(--accent);border-radius:10px;padding:20px;margin-bottom:20px;display:flex;align-items:center;gap:20px}
.team-avatar{width:60px;height:60px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;flex-shrink:0}
.team-avatar.main{background:linear-gradient(135deg,var(--accent),var(--teal));color:white}
.team-info h3{font-size:16px;font-weight:700;margin-bottom:4px}
.team-info p{font-size:12px;color:var(--muted)}
.team-stats{display:flex;gap:16px;margin-top:8px}
.team-stat{font-size:11px}
.team-stat label{color:var(--muted);display:block;font-size:9px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}
.team-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.agent-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;display:flex;align-items:center;gap:12px}
.agent-avatar{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700;color:white;flex-shrink:0}
.agent-info h4{font-size:13px;font-weight:600;margin-bottom:2px}
.agent-info p{font-size:11px;color:var(--muted)}
.role-tag{font-size:9px;padding:2px 6px;border-radius:3px;background:var(--surface2);color:var(--muted);text-transform:uppercase;letter-spacing:.5px}

/* ===== PROJECTS ===== */
.project-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.project-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
.project-card h3{font-size:14px;font-weight:700;margin-bottom:6px}
.project-card .desc{font-size:11px;color:var(--muted);margin:8px 0}
.project-card .note{font-size:11px;color:var(--muted);font-style:italic;margin-top:8px;padding-top:8px;border-top:1px solid var(--border)}
.project-meta{display:flex;gap:8px;align-items:center;margin-bottom:8px}

/* ===== RUNS ===== */
.run-status-row td{transition:background .15s}
.run-completed td{border-left:3px solid var(--green)}
.run-failed td{border-left:3px solid var(--red);background:rgba(239,68,68,.03)}
.run-running td{border-left:3px solid var(--yellow);animation:pulseY 2s infinite}
@keyframes pulseY{0%,100%{opacity:1}50%{opacity:.6}}
.output-preview{max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--muted)}

/* ===== PAUSED BANNER ===== */
.paused-banner{background:rgba(234,179,8,.12);border:1px solid rgba(234,179,8,.3);border-radius:8px;padding:14px 18px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.paused-banner-left{display:flex;align-items:center;gap:10px}
.paused-icon{width:20px;height:20px;color:var(--yellow);flex-shrink:0}
.paused-info{font-size:12px;color:var(--yellow)}
.paused-info strong{font-weight:700}
.paused-info .paused-detail{font-size:11px;color:var(--muted);margin-top:2px}
.retry-btn{background:rgba(234,179,8,.2);border:1px solid rgba(234,179,8,.4);color:var(--yellow);padding:6px 14px;border-radius:5px;font-size:11px;font-family:inherit;font-weight:600;cursor:pointer;transition:all .15s;white-space:nowrap}
.retry-btn:hover{background:rgba(234,179,8,.35);border-color:var(--yellow)}
.retry-btn:disabled{opacity:.5;cursor:not-allowed}

/* Mobile */
@media(max-width:768px){
  .sidebar{width:56px}
  .sidebar .nav-label,.sidebar-brand small,.sidebar-footer{display:none}
  .sidebar-brand{padding:12px 8px;font-size:9px;text-align:center;letter-spacing:1px}
  .nav-item{padding:12px;justify-content:center;gap:0}
  .nav-item .nav-icon{width:20px;height:20px}
  .main{margin-left:56px}
  .kanban{grid-template-columns:1fr}
  .memory-layout{grid-template-columns:1fr}
  .memory-sidebar{display:none}
  .office-layout{grid-template-columns:1fr}
}
</style>
</head>
<body>

<!-- Sidebar -->
<div class="sidebar">
  <div class="sidebar-brand">
    MISSION<br>CONTROL
    <small>OpenClaw v1</small>
  </div>
  <nav class="sidebar-nav">
    <div class="nav-item active" data-panel="tasks" onclick="switchPanel('tasks')">
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      <span class="nav-label">Tasks</span>
    </div>
    <div class="nav-item" data-panel="calendar" onclick="switchPanel('calendar')">
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
      <span class="nav-label">Calendar</span>
    </div>
    <div class="nav-item" data-panel="memory" onclick="switchPanel('memory')">
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
      <span class="nav-label">Memory</span>
    </div>
    <div class="nav-item" data-panel="office" onclick="switchPanel('office')">
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><polyline points="17,2 12,7 7,2"/></svg>
      <span class="nav-label">Office</span>
    </div>
    <div class="nav-item" data-panel="team" onclick="switchPanel('team')">
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg>
      <span class="nav-label">Team</span>
    </div>
    <div class="nav-item" data-panel="projects" onclick="switchPanel('projects')">
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
      <span class="nav-label">Projects</span>
    </div>
    <div class="nav-item" data-panel="runs" onclick="switchPanel('runs')">
      <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="13,2 3,14 12,14 11,22 21,10 12,10 13,2"/></svg>
      <span class="nav-label">Runs</span>
    </div>
  </nav>
  <div class="sidebar-footer">
    <div class="status-row">
      <span class="status-dot" id="sidebarDot"></span>
      <span id="sidebarStatus">Idle</span>
    </div>
  </div>
</div>

<!-- Main Content -->
<div class="main">
  <div class="main-header">
    <div class="main-title" id="panelTitle">Tasks Board</div>
    <div class="clock" id="clock"></div>
  </div>

  <!-- PAUSED TASKS BANNER -->
  <div class="paused-banner" id="pausedBanner" style="display:none">
    <div class="paused-banner-left">
      <svg class="paused-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      <div class="paused-info">
        <strong id="pausedCount">0</strong> task(s) paused due to rate limits
        <div class="paused-detail" id="pausedDetail"></div>
      </div>
    </div>
    <button class="retry-btn" id="retryBtn" onclick="retryNow()">Retry Now</button>
  </div>

  <!-- TASKS -->
  <div class="panel-view active" id="panel-tasks">
    <div class="kanban-progress" id="kanbanProgress"></div>
    <div class="kanban" id="kanbanBoard"></div>
  </div>

  <!-- CALENDAR -->
  <div class="panel-view" id="panel-calendar">
    <div id="calendarContent"></div>
  </div>

  <!-- MEMORY -->
  <div class="panel-view" id="panel-memory">
    <div class="memory-layout">
      <div class="memory-sidebar" id="memorySidebar"></div>
      <div class="memory-main" id="memoryMain"></div>
    </div>
  </div>

  <!-- OFFICE -->
  <div class="panel-view" id="panel-office">
    <div class="office-layout">
      <div class="office-canvas" id="officeCanvas"></div>
      <div class="office-sidebar" id="officeSidebar"></div>
    </div>
  </div>

  <!-- TEAM -->
  <div class="panel-view" id="panel-team">
    <div id="teamContent"></div>
  </div>

  <!-- PROJECTS -->
  <div class="panel-view" id="panel-projects">
    <div id="projectsContent"></div>
  </div>

  <!-- RUNS -->
  <div class="panel-view" id="panel-runs">
    <div id="runsContent"></div>
  </div>
</div>

<script>
/* === Utilities === */
function esc(s){if(s==null)return'';let d=document.createElement('div');d.textContent=String(s);return d.innerHTML}
function relTime(iso){if(!iso)return'\u2014';let d=new Date(iso),now=new Date(),s=Math.floor((now-d)/1000);if(s<0){s=Math.abs(s);if(s<60)return'in '+s+'s';if(s<3600)return'in '+Math.floor(s/60)+'m';if(s<86400)return'in '+Math.floor(s/3600)+'h';return'in '+Math.floor(s/86400)+'d'}if(s<60)return s+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';if(s<86400)return Math.floor(s/3600)+'h ago';return Math.floor(s/86400)+'d ago'}
function countdown(iso){if(!iso)return'\u2014';let s=Math.floor((new Date(iso)-new Date())/1000);if(s<=0)return'now';if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m '+s%60+'s';if(s<86400)return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';return Math.floor(s/86400)+'d '+Math.floor((s%86400)/3600)+'h'}
function dur(a,b){if(!a||!b)return'\u2014';let s=Math.floor((new Date(b)-new Date(a))/1000);if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m '+s%60+'s';return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m'}
function statusBadge(s){const m={active:'badge-green',completed:'badge-green',ok:'badge-green',done:'badge-green',in_progress:'badge-yellow',working:'badge-yellow',review:'badge-purple',queued:'badge-muted',idle:'badge-muted',backlog:'badge-muted',paused:'badge-yellow',failed:'badge-red',error:'badge-red',blocked:'badge-red'};return'<span class="badge '+(m[s]||'badge-purple')+'">'+esc(s)+'</span>'}
function priBadge(p){const m={critical:'badge-red',high:'badge-orange',medium:'badge-yellow',low:'badge-muted'};return'<span class="badge '+(m[p]||'badge-muted')+'">'+esc(p||'\u2014')+'</span>'}
function priDotClass(p){return'pri-'+(p||'low')}
function cronHuman(expr){if(!expr)return'\u2014';const parts=expr.split(' ');const dows=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];if(parts.length<5)return expr;let h=parts[1],m=parts[0];let time=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');if(parts[4]!=='*'){let days=parts[4].split(',').map(d=>dows[parseInt(d)]||d).join(', ');return days+' @ '+time}if(parts[2]!=='*')return'Day '+parts[2]+' @ '+time;return'Daily @ '+time}
function sparkline(hist){if(!hist||!hist.length)return'';return'<span class="sparkline">'+hist.map(h=>'<span class="spark-dot spark-'+(h==='ok'?'ok':h==='fail'||h==='error'?'fail':'none')+'"></span>').join('')+'</span>'}
function cronType(name){const n=(name||'').toLowerCase();if(n.includes('scraper')||n.includes('scrape'))return'scraper';if(n.includes('watchlist')||n.includes('watch'))return'watchlist';return'other'}

let currentPanel='tasks';
let STATE={};

const panelTitles={tasks:'Tasks Board',calendar:'Calendar',memory:'Activity Log',office:'The Office',team:'Meet the Team',projects:'Projects',runs:'Recent Runs'};

function switchPanel(id){
  currentPanel=id;
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.panel===id));
  document.querySelectorAll('.panel-view').forEach(p=>p.classList.toggle('active',p.id==='panel-'+id));
  document.getElementById('panelTitle').textContent=panelTitles[id]||id;
  renderActivePanel();
}

function renderActivePanel(){
  const S=STATE;if(!S.agent)return;
  // Sidebar status
  const ag=S.agent||{};
  const dot=document.getElementById('sidebarDot');
  dot.className='status-dot '+(ag.status==='working'?'working':ag.status==='error'?'error':'idle');
  document.getElementById('sidebarStatus').textContent=ag.current_action||'Idle';

  renderPausedBanner(S);

  switch(currentPanel){
    case'tasks':renderTasks(S);break;
    case'calendar':renderCalendar(S);break;
    case'memory':renderMemory(S);break;
    case'office':renderOffice(S);break;
    case'team':renderTeam(S);break;
    case'projects':renderProjects(S);break;
    case'runs':renderRuns(S);break;
  }
}

/* === TASKS BOARD (Kanban) === */
function renderTasks(S){
  const tasks=S.tasks||{};
  const tkeys=Object.keys(tasks);
  const cols={backlog:[],in_progress:[],review:[],completed:[]};
  const colMap={backlog:'backlog',queued:'backlog',in_progress:'in_progress',working:'in_progress',paused:'in_progress',review:'review',completed:'completed',done:'completed'};
  for(const k of tkeys){const t=tasks[k];const col=colMap[t.status]||'backlog';cols[col].push({...t,id:k})}
  const total=tkeys.length;
  const done=cols.completed.length;
  const pct=total?Math.round(done/total*100):0;

  document.getElementById('kanbanProgress').innerHTML=total?
    '<div class="kanban-progress-label"><span>'+done+' of '+total+' tasks complete</span><span>'+pct+'%</span></div><div class="progress-bar"><div class="progress-fill" style="width:'+pct+'%"></div></div>':'';

  const colNames={backlog:'Backlog',in_progress:'In Progress',review:'Review',completed:'Done'};
  let h='';
  for(const[col,items]of Object.entries(cols)){
    h+='<div class="kanban-col"><div class="kanban-col-header"><span>'+colNames[col]+'</span><span class="count">'+items.length+'</span></div><div class="kanban-col-body">';
    if(!items.length)h+='<div class="kanban-empty">No tasks</div>';
    for(const t of items){
      h+='<div class="task-card"><div class="task-card-title">'+esc(t.title||t.id)+'</div><div class="task-card-meta">';
      h+='<span class="pri-dot '+priDotClass(t.priority)+'"></span>';
      if(t.project||t.project_id)h+='<span class="task-card-project">'+esc(t.project||t.project_id)+'</span>';
      h+=priBadge(t.priority);
      h+='</div></div>'}
    h+='</div></div>'}
  document.getElementById('kanbanBoard').innerHTML=h||'<div class="empty-state">No tasks yet</div>';
}

/* === CALENDAR === */
function renderCalendar(S){
  const crons=S.crons||{};
  const ckeys=Object.keys(crons);
  if(!ckeys.length){document.getElementById('calendarContent').innerHTML='<div class="empty-state">No scheduled jobs</div>';return}

  let h='';
  // Always Running section
  h+='<div class="cal-section"><div class="cal-section-title">Scheduled Jobs</div>';
  for(const k of ckeys){
    const c=crons[k];
    const typ=cronType(c.name);
    const dotCls=c.last_status==='ok'?'ok':(c.last_status==='fail'||c.last_status==='error')?'fail':'unknown';
    h+='<div class="cron-card"><div class="cron-card-left"><span class="cron-status-dot '+dotCls+'"></span><div class="cron-info"><h4>'+esc(c.name||k)+'</h4><span>'+esc(cronHuman(c.schedule))+'</span></div></div>';
    h+='<div class="cron-card-right"><div class="cron-countdown">'+countdown(c.next_run)+'</div><div>until next run</div>'+sparkline(c.history)+'</div></div>'}
  h+='</div>';

  // Weekly grid
  h+='<div class="cal-section"><div class="cal-section-title">This Week</div><div class="cal-grid">';
  const dayNames=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  for(const d of dayNames)h+='<div class="cal-day-header">'+d+'</div>';

  const today=new Date();
  const dow=today.getDay();
  const monday=new Date(today);monday.setDate(today.getDate()-(dow===0?6:dow-1));

  for(let i=0;i<7;i++){
    const day=new Date(monday);day.setDate(monday.getDate()+i);
    const dayNum=day.getDate();
    const isToday=day.toDateString()===today.toDateString();
    h+='<div class="cal-day" style="'+(isToday?'border-color:var(--accent);':'')+'"><div class="cal-day-num"'+(isToday?' style="color:var(--accent)"':'')+'>'+dayNum+'</div>';
    // Check which crons run on this day
    const jsDow=day.getDay(); // 0=Sun
    for(const k of ckeys){
      const c=crons[k];
      const parts=(c.schedule||'').split(' ');
      if(parts.length<5)continue;
      let runs=false;
      if(parts[4]==='*')runs=true;
      else{const days=parts[4].split(',').map(Number);if(days.includes(jsDow))runs=true}
      if(runs){
        const typ=cronType(c.name);
        h+='<div class="cal-block '+typ+'">'+esc((c.name||k).substring(0,20))+'</div>'}}
    h+='</div>'}
  h+='</div></div>';
  document.getElementById('calendarContent').innerHTML=h;
}

/* === MEMORY (Activity Log) === */
function renderMemory(S){
  const logs=(S.activity_log||[]).slice(-200).reverse();
  // Count by level
  const counts={info:0,warn:0,error:0};
  for(const e of logs)counts[e.level||'info']=(counts[e.level||'info']||0)+1;

  let sb='<h4>Long Term Memory</h4>';
  sb+='<div class="mem-cat active" onclick="filterLogs(\'all\')">All Entries <span class="mem-count">'+logs.length+'</span></div>';
  sb+='<div class="mem-cat" onclick="filterLogs(\'info\')">Info <span class="mem-count">'+counts.info+'</span></div>';
  sb+='<div class="mem-cat" onclick="filterLogs(\'warn\')">Warnings <span class="mem-count">'+counts.warn+'</span></div>';
  sb+='<div class="mem-cat" onclick="filterLogs(\'error\')">Errors <span class="mem-count">'+counts.error+'</span></div>';
  document.getElementById('memorySidebar').innerHTML=sb;

  let h='<input class="memory-search" placeholder="Search activity log..." oninput="searchLogs(this.value)"/>';
  if(!logs.length)h+='<div class="empty-state">No activity recorded</div>';
  for(const e of logs){
    const lvl=e.level||'info';
    const lb=lvl==='error'?'badge-red':lvl==='warn'?'badge-yellow':'badge-purple';
    h+='<div class="log-entry" data-level="'+esc(lvl)+'" onclick="this.classList.toggle(\'open\')"><div class="log-header"><span class="log-ts">'+esc(e.timestamp)+'</span><span class="badge '+lb+'">'+esc(lvl.toUpperCase())+'</span></div><div class="log-msg">'+esc(e.message)+'</div><div class="log-expanded">'+esc(JSON.stringify(e,null,2))+'</div></div>'}
  document.getElementById('memoryMain').innerHTML=h;
}

function filterLogs(level){
  document.querySelectorAll('.mem-cat').forEach(c=>c.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('#memoryMain .log-entry').forEach(e=>{
    if(level==='all')e.style.display='';
    else e.style.display=e.dataset.level===level?'':'none';
  });
}
function searchLogs(q){
  const ql=q.toLowerCase();
  document.querySelectorAll('#memoryMain .log-entry').forEach(e=>{
    e.style.display=e.textContent.toLowerCase().includes(ql)?'':'none';
  });
}

/* === OFFICE === */
function renderOffice(S){
  const ag=S.agent||{};
  const isWorking=ag.status==='working';
  const canvas=document.getElementById('officeCanvas');

  // Draw pixel office with HTML/CSS
  let h='<div class="office-floor pixel-font" style="background:repeating-conic-gradient(#1a1a2e 0% 25%, #16213e 0% 50%) 0 0/40px 40px;width:100%;height:100%;position:relative;padding:20px;">';

  // Desk
  h+='<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center">';
  // Desk surface
  h+='<div style="background:#2a1a3e;border:2px solid #3a2a4e;border-radius:4px;padding:8px 30px;margin-bottom:8px;position:relative">';
  // Monitor
  h+='<div style="background:#0a0a1a;border:2px solid #4a3a5e;border-radius:3px;width:80px;height:50px;margin:0 auto 4px;display:flex;align-items:center;justify-content:center;font-size:6px;color:var(--green)">';
  h+=isWorking?'<span style="animation:pulse 1s infinite">RUNNING</span>':'IDLE';
  h+='</div>';
  // Monitor stand
  h+='<div style="width:20px;height:8px;background:#4a3a5e;margin:0 auto;border-radius:0 0 3px 3px"></div>';
  h+='</div>';

  // Avatar
  h+='<div style="margin-top:8px;position:relative;display:inline-block">';
  // Speech bubble
  if(ag.current_action){
    h+='<div style="position:absolute;bottom:100%;left:50%;transform:translateX(-50%);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:7px;white-space:nowrap;margin-bottom:6px;color:var(--text);font-family:\'Press Start 2P\',monospace">'+esc(ag.current_action)+'</div>'}
  // Character
  h+='<div style="width:32px;height:32px;background:linear-gradient(135deg,var(--accent),var(--teal));border-radius:6px;margin:0 auto;display:flex;align-items:center;justify-content:center;font-size:12px;color:white;font-weight:bold;position:relative">J';
  // Status dot
  h+='<span class="status-dot '+(isWorking?'working':'idle')+'" style="position:absolute;bottom:-2px;right:-2px;width:10px;height:10px;border:2px solid var(--bg)"></span>';
  h+='</div>';
  h+='<div style="font-size:7px;margin-top:4px;color:var(--accent);text-align:center;font-family:\'Press Start 2P\',monospace">JARVIS</div>';
  h+='</div>';
  h+='</div></div>';
  canvas.innerHTML=h;

  // Right sidebar
  let rs='';
  // Demo controls
  rs+='<div class="office-panel"><h4>Demo Controls</h4>';
  rs+='<button class="demo-btn" onclick="demoAction(\'working\',\'Processing task...\')">Start Working</button>';
  rs+='<button class="demo-btn" onclick="demoAction(\'idle\',\'Waiting for instructions\')">Go Idle</button>';
  rs+='<button class="demo-btn" onclick="demoAction(\'working\',\'Researching market data\')">Research Mode</button>';
  rs+='<button class="demo-btn" onclick="demoAction(\'error\',\'Connection timeout\')">Simulate Error</button>';
  rs+='</div>';

  // Live activity
  rs+='<div class="office-panel activity-mini"><h4>Live Activity</h4>';
  const logs=(S.activity_log||[]).slice(-5).reverse();
  if(!logs.length)rs+='<div style="font-size:10px;color:var(--muted)">No activity</div>';
  for(const e of logs){
    rs+='<div class="log-mini">'+esc((e.message||'').substring(0,50))+'</div>'}
  rs+='</div>';
  document.getElementById('officeSidebar').innerHTML=rs;
}

function demoAction(status,action){
  fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:{status,current_action:action,last_active:new Date().toISOString()}})});
}

/* === TEAM === */
function renderTeam(S){
  const ag=S.agent||{};
  const agents=S.agents||{};
  const agentKeys=Object.keys(agents);

  let h='';
  // Featured: Jarvis
  h+='<div class="team-featured"><div class="team-avatar main">J</div><div class="team-info"><h3>Jarvis</h3><p>Primary Agent — Orchestrator</p>';
  h+='<div style="margin-top:6px">'+statusBadge(ag.status||'idle')+' <span class="role-tag">Orchestrator</span></div>';
  h+='<div class="team-stats"><div class="team-stat"><label>Status</label>'+esc(ag.current_action||'Idle')+'</div>';
  h+='<div class="team-stat"><label>Last Active</label>'+relTime(ag.last_active)+'</div></div>';
  h+='</div></div>';

  // Agent roster
  if(agentKeys.length){
    h+='<h3 style="font-size:14px;margin-bottom:12px">Sub-Agents</h3><div class="team-grid">';
    const colors=['#7c3aed','#14b8a6','#f97316','#ef4444','#22c55e','#eab308'];
    let ci=0;
    for(const k of agentKeys){
      const a=agents[k];
      const color=colors[ci%colors.length];ci++;
      const initial=(a.name||k).charAt(0).toUpperCase();
      h+='<div class="agent-card"><div class="agent-avatar" style="background:'+color+'">'+initial+'</div><div class="agent-info"><h4>'+esc(a.name||k)+'</h4>';
      h+='<p>'+statusBadge(a.status||'idle')+' <span class="role-tag">'+esc(a.role||'Agent')+'</span></p>';
      h+='<p>Last active: '+relTime(a.last_active)+'</p></div></div>'}
    h+='</div>';
  } else {
    h+='<div style="margin-top:16px;padding:20px;text-align:center;color:var(--muted);font-size:12px;background:var(--surface);border:1px solid var(--border);border-radius:8px">No sub-agents spawned. Jarvis is operating solo.</div>';
  }
  document.getElementById('teamContent').innerHTML=h;
}

/* === PROJECTS === */
function renderProjects(S){
  const proj=S.projects||{};
  const pkeys=Object.keys(proj);
  if(!pkeys.length){document.getElementById('projectsContent').innerHTML='<div class="empty-state">No active projects</div>';return}

  let h='<div class="project-grid">';
  for(const k of pkeys){
    const p=proj[k];
    let total=0,done=0;
    for(const tk of Object.keys(S.tasks||{})){const t=(S.tasks||{})[tk];if(t.project===k||t.project_id===k){total++;if(t.status==='completed'||t.status==='done')done++}}
    const pct=total?Math.round(done/total*100):0;
    const statusColor={active:'badge-green',paused:'badge-yellow',blocked:'badge-red',completed:'badge-muted'};
    h+='<div class="project-card"><h3>'+esc(p.name||k)+'</h3>';
    h+='<div class="project-meta"><span class="badge '+(statusColor[p.status]||'badge-purple')+'">'+esc(p.status||'active')+'</span></div>';
    h+='<div class="progress-bar"><div class="progress-fill" style="width:'+pct+'%"></div></div>';
    h+='<div style="font-size:11px;color:var(--muted);margin-top:4px">'+done+'/'+total+' tasks ('+pct+'%)</div>';
    if(p.description)h+='<div class="desc">'+esc(p.description)+'</div>';
    if(p.note)h+='<div class="note">'+esc(p.note)+'</div>';
    h+='</div>'}
  h+='</div>';
  document.getElementById('projectsContent').innerHTML=h;
}

/* === RUNS === */
function renderRuns(S){
  const tasks=S.tasks||{};
  const tkeys=Object.keys(tasks);
  // Sort by started_at descending
  const sorted=tkeys.map(k=>({...tasks[k],id:k})).filter(t=>t.started_at).sort((a,b)=>new Date(b.started_at)-new Date(a.started_at));

  if(!sorted.length){document.getElementById('runsContent').innerHTML='<div class="empty-state">No task executions recorded</div>';return}

  let h='<table><thead><tr><th>Task</th><th>Project</th><th>Started</th><th>Duration</th><th>Status</th><th>Output</th></tr></thead><tbody>';
  for(const t of sorted){
    const rowCls=t.status==='completed'||t.status==='done'?'run-completed':t.status==='failed'||t.status==='error'?'run-failed':t.status==='in_progress'||t.status==='working'?'run-running':'';
    h+='<tr class="'+rowCls+'"><td>'+esc(t.title||t.id)+'</td><td>'+esc(t.project||t.project_id||'\u2014')+'</td><td>'+relTime(t.started_at)+'</td><td>'+dur(t.started_at,t.completed_at||new Date().toISOString())+'</td><td>'+statusBadge(t.status)+'</td><td class="output-preview" title="'+esc(t.output||'')+'">'+esc((t.output||'\u2014').substring(0,60))+'</td></tr>'}
  h+='</tbody></table>';
  document.getElementById('runsContent').innerHTML=h;
}

/* === Paused Banner === */
function renderPausedBanner(S){
  const banner=document.getElementById('pausedBanner');
  const ag=S.agent||{};
  const paused=ag.paused_tasks||0;
  if(paused<=0){banner.style.display='none';return}
  banner.style.display='flex';
  document.getElementById('pausedCount').textContent=paused;
  // Calculate next retry (top of next hour)
  const now=new Date();
  const nextHour=new Date(now);nextHour.setMinutes(0,0,0);nextHour.setHours(nextHour.getHours()+1);
  const retryTime=nextHour.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  document.getElementById('pausedDetail').textContent='Will auto-retry at '+retryTime;
}

function retryNow(){
  const btn=document.getElementById('retryBtn');
  btn.disabled=true;btn.textContent='Retrying...';
  fetch('/retry',{method:'POST'}).then(r=>r.json()).then(d=>{
    btn.textContent=d.ok?'Retry triggered':'Retry failed';
    setTimeout(()=>{btn.disabled=false;btn.textContent='Retry Now'},5000);
  }).catch(()=>{btn.disabled=false;btn.textContent='Retry Now'});
}

/* === Clock === */
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleString()},1000);
document.getElementById('clock').textContent=new Date().toLocaleString();

/* === SSE === */
const evtSource=new EventSource("/events");
evtSource.onmessage=()=>fetch("/state").then(r=>r.json()).then(s=>{STATE=s;renderActivePanel()});

/* === Initial load === */
fetch("/state").then(r=>r.json()).then(s=>{STATE=s;renderActivePanel()});

/* === Countdown refresh === */
setInterval(()=>{if(currentPanel==='calendar')renderCalendar(STATE)},30000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence request logs

    def _headers(self, code=200, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        if self.path == "/":
            self._headers(200, "text/html; charset=utf-8")
            self.wfile.write(DASHBOARD_HTML.encode())

        elif self.path == "/state":
            state = read_state()
            self._headers(200)
            self.wfile.write(json.dumps(state, indent=2).encode())

        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            cl = threading.Lock()
            sse_clients.append((self.wfile, cl))
            try:
                while True:
                    time.sleep(15)
                    with cl:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except Exception:
                pass

        else:
            self._headers(404)
            self.wfile.write(json.dumps({"error": "not found"}).encode())

    def do_POST(self):
        if self.path == "/update":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                patch = json.loads(body)
            except json.JSONDecodeError:
                self._headers(400)
                self.wfile.write(json.dumps({"error": "invalid json"}).encode())
                return

            with lock:
                state = json.loads(STATE_PATH.read_text())
                deep_merge(state, patch)
                STATE_PATH.write_text(json.dumps(state, indent=2))

            notify_sse()
            self._headers(200)
            self.wfile.write(json.dumps({"ok": True}).encode())

        elif self.path == "/retry":
            # Trigger resume_worker.sh immediately
            resume_script = Path(__file__).parent / "resume_worker.sh"
            if resume_script.exists():
                try:
                    subprocess.Popen(
                        ["bash", str(resume_script)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._headers(200)
                    self.wfile.write(json.dumps({"ok": True, "message": "Resume worker triggered"}).encode())
                except Exception as e:
                    self._headers(500)
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
            else:
                self._headers(404)
                self.wfile.write(json.dumps({"ok": False, "error": "resume_worker.sh not found"}).encode())

        else:
            self._headers(404)
            self.wfile.write(json.dumps({"error": "not found"}).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    server = HTTPServer(("0.0.0.0", 4242), Handler)
    server.daemon_threads = True
    print(f"Mission Control running on http://localhost:4242")
    server.serve_forever()


if __name__ == "__main__":
    main()
