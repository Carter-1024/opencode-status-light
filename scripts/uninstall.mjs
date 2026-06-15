import { rm, readFile, writeFile } from "node:fs/promises"
import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const configDir = join(homedir(), ".config/opencode")
const installDir = join(configDir, "status-light")
const pluginTarget = join(installDir, "status-light.ts")
const configPath = join(configDir, "opencode.jsonc")
const pluginSpec = `file://${pluginTarget}`
const legacySpecs = [`file://${join(configDir, "status-light.ts")}`]

function parseJsonc(text) {
  try {
    return JSON.parse(text)
  } catch {
    return JSON.parse(text.replace(/\/\*[\s\S]*?\*\//g, ""))
  }
}

if (existsSync(configPath)) {
  const config = parseJsonc(await readFile(configPath, "utf8"))
  if (Array.isArray(config.plugin)) config.plugin = config.plugin.filter((item) => item !== pluginSpec && !legacySpecs.includes(item))
  await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8")
}

await rm(installDir, { recursive: true, force: true })
console.log("Uninstalled opencode status light. Restart opencode if it is running.")
