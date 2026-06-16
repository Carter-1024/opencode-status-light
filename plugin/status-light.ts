import { readFile, writeFile } from "node:fs/promises"
import { spawn } from "node:child_process"
import { join } from "node:path"
import { homedir, tmpdir } from "node:os"
import type { Plugin } from "@opencode-ai/plugin"

type LightColor = "green" | "yellow" | "red" | "gray"
type OpencodeState =
  | "waiting-input"
  | "thinking"
  | "running-tool"
  | "tool-completed"
  | "responding"
  | "session-updated"
  | "permission-wait"
  | "permission-resolved"
  | "error"
  | "unknown"

type Status = {
  state: OpencodeState
  color: LightColor
  label: string
  detail?: string
  sessionID?: string
  messageID?: string
  tool?: string
  eventType?: string
  projectDirectory?: string
  updatedAt: string
}

type UsageResult = { success?: boolean; output?: string; error?: string } | null | undefined

const INSTALL_DIR = join(homedir(), ".config/opencode/status-light")
const STATUS_FILE = process.env.OPENCODE_STATUS_LIGHT_FILE ?? join(tmpdir(), "opencode-status-light.json")
const QUOTA_FILE = process.env.OPENCODE_STATUS_LIGHT_QUOTA_FILE ?? join(tmpdir(), "opencode-quota-status.json")
const UI_SCRIPT = process.env.OPENCODE_STATUS_LIGHT_UI ?? join(INSTALL_DIR, "status-light.py")
const QUOTA_REFRESH_MS = Number(process.env.OPENCODE_STATUS_LIGHT_QUOTA_REFRESH_MS ?? 300_000)
const QUOTA_MIN_REFRESH_MS = Number(process.env.OPENCODE_STATUS_LIGHT_QUOTA_MIN_REFRESH_MS ?? 15_000)
const MYSTATUS_DIR = join(homedir(), ".config/opencode/node_modules/opencode-mystatus/dist/plugin")

let lastStatus: Status = {
  state: "unknown",
  color: "gray",
  label: "opencode 未启动或暂无事件",
  updatedAt: new Date().toISOString(),
}
let quotaRefreshRunning = false
let lastQuotaRefresh = 0

function asObject(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object") return undefined
  return value as Record<string, unknown>
}

function findEvent(input: unknown): Record<string, unknown> {
  const obj = asObject(input) ?? {}
  return asObject(obj.event) ?? obj
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined
}

function statusFromEvent(input: unknown, projectDirectory: string): Status | undefined {
  const event = findEvent(input)
  const eventType = stringValue(event.type)
  const data = asObject(event.data) ?? event
  const part = asObject(data.part)
  const info = asObject(data.info)
  const state = asObject(part?.state)
  const time = asObject(part?.time)
  const partType = stringValue(part?.type)
  const toolStatus = stringValue(state?.status)
  const finish = stringValue(part?.reason) ?? stringValue(info?.finish)
  const sessionID = stringValue(data.sessionID) ?? stringValue(part?.sessionID) ?? stringValue(info?.sessionID)
  const messageID = stringValue(part?.messageID) ?? stringValue(info?.id)
  const tool = stringValue(part?.tool)

  const base = { sessionID, messageID, tool, eventType, projectDirectory, updatedAt: new Date().toISOString() }
  const isPermissionEvent = eventType?.includes("permission") || partType === "permission"
  const isPermissionResolved = eventType?.includes("replied") || eventType?.includes("resolved")

  if (isPermissionEvent && !isPermissionResolved) {
    return { ...base, state: "permission-wait", color: "red", label: "等待权限确认", detail: "opencode 正在等待你批准操作" }
  }
  if (isPermissionEvent && isPermissionResolved) {
    return { ...base, state: "permission-resolved", color: "yellow", label: "继续处理", detail: "权限已确认，opencode 继续执行" }
  }
  if (toolStatus === "failed" || toolStatus === "error" || finish === "error") {
    return { ...base, state: "error", color: "red", label: "执行出错", detail: tool ? `${tool} 失败` : "opencode 报告错误" }
  }
  if (partType === "tool" && (toolStatus === "running" || toolStatus === "pending")) {
    return { ...base, state: "running-tool", color: "yellow", label: "正在执行工具", detail: tool ? `工具：${tool}` : "工具调用进行中" }
  }
  if (partType === "tool" && toolStatus === "completed") {
    return { ...base, state: "tool-completed", color: "yellow", label: "工具完成", detail: tool ? `${tool} 执行完成，整理结果中` : "工具执行完成，整理结果中" }
  }
  if (partType === "reasoning") {
    return { ...base, state: "thinking", color: "yellow", label: "正在思考", detail: "模型正在生成 reasoning" }
  }
  if (partType === "text") {
    return { ...base, state: "responding", color: time?.end ? "green" : "yellow", label: time?.end ? "回复完成" : "正在回复", detail: time?.end ? "assistant 已输出文本" : "assistant 正在输出文本" }
  }
  if (partType === "step-start") {
    return { ...base, state: "thinking", color: "yellow", label: "正在处理", detail: "新的 assistant step 已开始" }
  }
  if (partType === "step-finish" || (info?.time && asObject(info.time)?.completed)) {
    return { ...base, state: "waiting-input", color: "green", label: "等待输入", detail: finish ? `上一步结束：${finish}` : "思考结束，等待你的下一条消息" }
  }
  if (eventType === "session.updated.1") {
    return { ...base, state: "session-updated", color: "green", label: "会话已更新", detail: "opencode 已同步会话状态" }
  }
  return undefined
}

async function writeStatus(status: Status) {
  lastStatus = status
  await writeFile(STATUS_FILE, `${JSON.stringify(status, null, 2)}\n`, "utf8")
}

function platformSummary(platform: string, output: string) {
  const extractPercent = (text: string) => {
    const prefixed = text.match(/(?:剩余|remaining)\s*(\d+)%/i)?.[1]
    if (prefixed) return prefixed

    const suffixed = text.match(/(\d+)%\s*remaining/i)?.[1]
    if (suffixed) return suffixed

    return text.match(/(\d+)%/)?.[1]
  }

  const account = output.match(/Account:\s*(.+)/)?.[1]?.trim()
  const percent = extractPercent(output)
  const lines = output.split(/\r?\n/).map((line) => line.trim())
  let weeklyPercent: number | undefined

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const linePercent = extractPercent(line)
    if (!linePercent) continue

    const heading = lines.slice(0, index).reverse().find(Boolean)
    if (!heading) continue
    if (!/(?:7\s*(?:天|day|days)|周)/i.test(heading)) continue

    weeklyPercent = Number(linePercent)
    break
  }

  return {
    platform,
    account,
    remainingPercent: percent ? Number(percent) : undefined,
    weeklyRemainingPercent: weeklyPercent,
    summary: `${platform}${percent ? ` ${percent}%` : ""}${weeklyPercent !== undefined ? ` 周${weeklyPercent}%` : ""}${account ? ` ${account}` : ""}`,
  }
}

function isAssistantTurnCompleted(input: unknown) {
  const event = findEvent(input)
  const eventType = stringValue(event.type)
  const data = asObject(event.data) ?? event
  const part = asObject(data.part)
  const info = asObject(data.info)
  const infoTime = asObject(info?.time)
  if (part?.type === "step-finish") return true
  if (eventType === "message.updated.1" && info?.role === "assistant" && infoTime?.completed) return true
  return false
}

async function importMystatusModules() {
  const [openai, zhipu, google, copilot] = await Promise.all([
    import(join(MYSTATUS_DIR, "lib/openai.js")),
    import(join(MYSTATUS_DIR, "lib/zhipu.js")),
    import(join(MYSTATUS_DIR, "lib/google.js")),
    import(join(MYSTATUS_DIR, "lib/copilot.js")),
  ])
  return { openai, zhipu, google, copilot }
}

async function updateQuotaStatus(force = false) {
  const now = Date.now()
  if (!force && now - lastQuotaRefresh < QUOTA_MIN_REFRESH_MS) return
  if (quotaRefreshRunning) return
  quotaRefreshRunning = true
  lastQuotaRefresh = now

  try {
    const authPath = join(homedir(), ".local/share/opencode/auth.json")
    const authData = JSON.parse(await readFile(authPath, "utf8"))
    const { openai, zhipu, google, copilot } = await importMystatusModules()
    const queried = await Promise.all([
      openai.queryOpenAIUsage(authData.openai).then((result: UsageResult) => ["OpenAI", result] as const),
      zhipu.queryZhipuUsage(authData["zhipuai-coding-plan"]).then((result: UsageResult) => ["Zhipu", result] as const),
      zhipu.queryZaiUsage(authData["zai-coding-plan"]).then((result: UsageResult) => ["Z.ai", result] as const),
      google.queryGoogleUsage().then((result: UsageResult) => ["Google", result] as const),
      copilot.queryCopilotUsage(authData["github-copilot"]).then((result: UsageResult) => ["Copilot", result] as const),
    ])
    const accounts = queried
      .filter((entry) => entry[1]?.success && entry[1].output)
      .map(([platform, result]) => platformSummary(platform, result!.output!))
    const errors = queried.filter((entry) => entry[1]?.error).map(([platform, result]) => `${platform}: ${result!.error}`)
    await writeFile(QUOTA_FILE, `${JSON.stringify({ accounts, errors, label: accounts.length ? accounts.map((item) => item.summary).join(" · ") : "未找到额度信息", updatedAt: new Date().toISOString() }, null, 2)}\n`, "utf8")
  } catch (error) {
    await writeFile(QUOTA_FILE, `${JSON.stringify({ accounts: [], errors: [error instanceof Error ? error.message : String(error)], label: "额度读取失败", updatedAt: new Date().toISOString() }, null, 2)}\n`, "utf8")
  } finally {
    quotaRefreshRunning = false
  }
}

function startQuotaRefresh() {
  updateQuotaStatus(true)
  setInterval(updateQuotaStatus, QUOTA_REFRESH_MS).unref()
}

function startFloatingUI() {
  if (process.env.OPENCODE_STATUS_LIGHT_AUTOSTART === "0") return
  if (!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) return
  const matcher = `${UI_SCRIPT} --status-file ${STATUS_FILE}`
  const pgrep = spawn("pgrep", ["-f", matcher], { stdio: "ignore" })
  pgrep.on("exit", (code) => {
    if (code === 0) return
    const child = spawn(UI_SCRIPT, ["--status-file", STATUS_FILE, "--quota-file", QUOTA_FILE], {
      detached: true,
      stdio: "ignore",
      env: { ...process.env, OPENCODE_STATUS_LIGHT_WINDOWID: process.env.WINDOWID ?? "" },
    })
    child.unref()
  })
}

export default (async ({ directory }) => {
  await writeStatus({ ...lastStatus, projectDirectory: directory, updatedAt: new Date().toISOString() })
  startQuotaRefresh()
  startFloatingUI()
  return {
    event: async (input) => {
      const status = statusFromEvent(input, directory)
      if (status) await writeStatus(status)
      if (isAssistantTurnCompleted(input)) void updateQuotaStatus(true)
    },
  }
}) satisfies Plugin
