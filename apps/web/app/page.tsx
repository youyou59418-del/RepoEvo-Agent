"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Task = {
  run_id: string;
  status: string;
  state_version: number;
  state?: Record<string, unknown>;
};

type StreamEvent = {
  name: string;
  payload: unknown;
  receivedAt: string;
};

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8080").replace(/\/$/, "");
const STREAM_EVENT_NAMES = [
  "created",
  "checkpoint",
  "claimed",
  "pause",
  "resume",
  "approved",
  "cancel",
  "worker_completed",
  "worker_failed",
  "timeout",
];

async function errorMessage(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;
}

function statusTone(status: string): string {
  if (status === "completed") return "#16a34a";
  if (status === "failed" || status === "cancelled") return "#dc2626";
  if (status === "paused") return "#d97706";
  return "#2563eb";
}

export default function Home() {
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [streamEpoch, setStreamEpoch] = useState(0);
  const [request, setRequest] = useState("修复订单服务中的一个小问题，并保留可审计的测试证据。");
  const [notice, setNotice] = useState("创建一个任务，观察安全的生命周期控制与 SSE 事件。");
  const [busy, setBusy] = useState(false);

  const refreshTask = useCallback(async (runId: string) => {
    const response = await fetch(`${API_BASE}/api/tasks/${runId}`);
    if (!response.ok) throw new Error(await errorMessage(response));
    setTask((await response.json()) as Task);
  }, []);

  async function createTask() {
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/tasks`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          task_id: "web-lifecycle-demo",
          initial_state: { request, demo_mode: "lifecycle_only" },
        }),
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      const created = (await response.json()) as Task;
      setTask(created);
      setEvents([]);
      setStreamEpoch((current) => current + 1);
      setNotice("任务已入队。你可以暂停、恢复、审批或取消它；所有命令都有幂等保护。");
    } catch (error) {
      setNotice(`创建失败：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  async function transition(action: "pause" | "resume" | "approve" | "cancel") {
    if (!task) return;
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/tasks/${task.run_id}/${action}`, { method: "POST" });
      if (!response.ok) throw new Error(await errorMessage(response));
      await refreshTask(task.run_id);
      setEvents([]);
      setStreamEpoch((current) => current + 1);
      setNotice(`${action} 命令已被 API 接受；请查看右侧的 SSE 事件流。`);
    } catch (error) {
      setNotice(`命令未执行：${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!task) return;
    const runId = task.run_id;
    const source = new EventSource(`${API_BASE}/api/tasks/${runId}/events?follow=true`);
    const append = (name: string, event: Event) => {
      const raw = (event as MessageEvent<string>).data;
      let payload: unknown = raw;
      try {
        payload = JSON.parse(raw) as unknown;
      } catch {
        // A non-JSON timeout or proxy message is still useful evidence.
      }
      setEvents((current) => [...current.slice(-49), { name, payload, receivedAt: new Date().toLocaleTimeString() }]);
      void refreshTask(runId).catch(() => undefined);
    };
    for (const name of STREAM_EVENT_NAMES) {
      source.addEventListener(name, (event) => append(name, event));
    }
    source.onmessage = (event) => append("message", event);
    source.onerror = () => source.close();
    return () => source.close();
  }, [refreshTask, streamEpoch, task?.run_id]);

  useEffect(() => {
    if (!task) return;
    const timer = window.setInterval(() => {
      void refreshTask(task.run_id).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [refreshTask, task?.run_id]);

  const actionState = useMemo(() => {
    const status = task?.status ?? "";
    return {
      pause: !task || busy || !["queued", "running"].includes(status),
      resume: !task || busy || status !== "paused",
      approve: !task || busy || !["queued", "running", "paused"].includes(status),
      cancel: !task || busy || !["queued", "running", "paused"].includes(status),
    };
  }, [busy, task]);

  return (
    <main style={{ display: "grid", gap: 20 }}>
      <section style={{ display: "grid", gap: 8 }}>
        <p style={{ color: "#2563eb", fontWeight: 700, letterSpacing: 1, margin: 0 }}>SAFE MAINTENANCE CONTROL PLANE</p>
        <h1 style={{ fontSize: "clamp(2rem, 5vw, 3.6rem)", letterSpacing: "-0.05em", margin: 0 }}>RepoEvo Agent</h1>
        <p style={{ color: "#475569", fontSize: 18, lineHeight: 1.6, margin: 0 }}>
          一个可恢复、可观测、受限工具执行的软件维护 Agent。此页面演示任务控制面；真实修复由受限 Worker 与沙箱执行。
        </p>
      </section>

      <section style={{ background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 16, padding: 16 }}>
        <strong>状态提示：</strong> {notice}
      </section>

      <section style={{ display: "grid", gap: 12, background: "white", border: "1px solid #e2e8f0", borderRadius: 16, padding: 20, boxShadow: "0 10px 30px rgba(15, 23, 42, 0.06)" }}>
        <label htmlFor="request" style={{ fontWeight: 700 }}>维护请求</label>
        <textarea
          id="request"
          value={request}
          onChange={(event) => setRequest(event.target.value)}
          rows={3}
          style={{ border: "1px solid #cbd5e1", borderRadius: 10, font: "inherit", padding: 12, resize: "vertical" }}
        />
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          <button onClick={createTask} disabled={busy} style={{ background: "#2563eb", border: 0, borderRadius: 9, color: "white", cursor: busy ? "wait" : "pointer", fontWeight: 700, padding: "10px 16px" }}>
            {busy ? "处理中…" : "创建生命周期任务"}
          </button>
          <button onClick={() => void transition("pause")} disabled={actionState.pause}>暂停</button>
          <button onClick={() => void transition("resume")} disabled={actionState.resume}>恢复</button>
          <button onClick={() => void transition("approve")} disabled={actionState.approve}>审批</button>
          <button onClick={() => void transition("cancel")} disabled={actionState.cancel}>取消</button>
          {task && <button onClick={() => void refreshTask(task.run_id)} disabled={busy}>刷新状态</button>}
        </div>
      </section>

      <section style={{ display: "grid", gap: 20, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
        <article style={{ background: "#0f172a", borderRadius: 16, color: "#e2e8f0", minHeight: 250, padding: 20 }}>
          <p style={{ color: "#94a3b8", fontWeight: 700, marginTop: 0 }}>当前任务</p>
          {task ? (
            <>
              <p style={{ color: statusTone(task.status), fontSize: 28, fontWeight: 800, margin: "8px 0" }}>{task.status}</p>
              <p style={{ overflowWrap: "anywhere" }}><strong>Run ID:</strong> {task.run_id}</p>
              <p><strong>State version:</strong> {task.state_version}</p>
              <pre style={{ background: "#172554", borderRadius: 10, maxHeight: 180, overflow: "auto", padding: 12 }}>{JSON.stringify(task.state ?? {}, null, 2)}</pre>
            </>
          ) : (
            <p>尚未创建任务。</p>
          )}
        </article>

        <article style={{ background: "white", border: "1px solid #e2e8f0", borderRadius: 16, minHeight: 250, padding: 20 }}>
          <p style={{ color: "#475569", fontWeight: 700, marginTop: 0 }}>SSE 审计事件</p>
          <div style={{ display: "grid", gap: 8, maxHeight: 310, overflow: "auto" }}>
            {events.length === 0 ? (
              <p style={{ color: "#64748b" }}>创建任务后，这里会按顺序显示后端附加的事件。</p>
            ) : events.map((event, index) => (
              <div key={`${event.receivedAt}-${index}`} style={{ borderLeft: "3px solid #60a5fa", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12, paddingLeft: 10 }}>
                <strong>{event.receivedAt} · {event.name}</strong>
                <div style={{ color: "#475569", overflowWrap: "anywhere" }}>{JSON.stringify(event.payload)}</div>
              </div>
            ))}
          </div>
        </article>
      </section>

      <p style={{ color: "#64748b", fontSize: 14, margin: 0 }}>
        API: {API_BASE} · 数据库与队列由 Compose 管理 · 浏览器只允许访问本机控制台来源。
      </p>
    </main>
  );
}
