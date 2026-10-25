// npm prepare 钩子：husky 必须在 git 仓库根执行（web/ 是子目录，直接跑会 ".git can't be found"）。
// 任何环境失败都只跳过、不让 npm install 失败（非 git 环境 / 依赖未装全）。
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const webDir = dirname(dirname(fileURLToPath(import.meta.url)))
const root = execFileSync('git', ['rev-parse', '--show-toplevel'], {
  cwd: webDir,
  encoding: 'utf8',
}).trim()
const huskyBin = join(webDir, 'node_modules', 'husky', 'bin.js')

if (!existsSync(huskyBin)) {
  console.log('(husky not installed, skip hooks setup)')
} else {
  try {
    execFileSync(process.execPath, [huskyBin], { cwd: root, stdio: 'inherit' })
  } catch (e) {
    console.log(`(husky skipped: ${e instanceof Error ? e.message.split('\n')[0] : e})`)
  }
}
