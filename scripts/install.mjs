import { copyFile, mkdir, readFile, writeFile, chmod } from "node:fs/promises"
import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { spawnSync } from "node:child_process"

const root = new URL("..", import.meta.url).pathname
const configDir = join(homedir(), ".config/opencode")
const installDir = join(configDir, "status-light")
const pluginTarget = join(installDir, "status-light.ts")
const uiTarget = join(installDir, "status-light.py")
const configPath = join(configDir, "opencode.jsonc")
const pluginSpec = `file://${pluginTarget}`
const legacySpecs = [
  `file://${join(configDir, "status-light.ts")}`,
]

function parseJsonc(text) {
  try {
    return JSON.parse(text)
  } catch {
    return JSON.parse(text.replace(/\/\*[\s\S]*?\*\//g, ""))
  }
}

async function readConfig() {
  if (!existsSync(configPath)) return { $schema: "https://opencode.ai/config.json" }
  return parseJsonc(await readFile(configPath, "utf8"))
}

await mkdir(installDir, { recursive: true })
await copyFile(join(root, "plugin/status-light.ts"), pluginTarget)
await copyFile(join(root, "status-light.py"), uiTarget)
await chmod(uiTarget, 0o755)

await mkdir(dirname(configPath), { recursive: true })
const config = await readConfig()
const plugins = Array.isArray(config.plugin) ? config.plugin : config.plugin ? [config.plugin] : []
config.plugin = [...plugins.filter((item) => item !== pluginSpec && !legacySpecs.includes(item)), pluginSpec]
await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8")

const npmInstall = spawnSync("npm", ["install", "--prefix", configDir, "opencode-mystatus", "@opencode-ai/plugin"], { stdio: "inherit" })
if (npmInstall.status !== 0) {
  throw new Error("Failed to install opencode status light npm dependencies")
}

console.log(`Installed opencode status light to ${installDir}`)
console.log("Restart opencode for the plugin to load.")
