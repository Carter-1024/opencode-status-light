import { access } from "node:fs/promises"

await access(new URL("../plugin/status-light.ts", import.meta.url))
await access(new URL("../status-light.py", import.meta.url))
console.log("ok")
