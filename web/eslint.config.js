// ESLint 平面配置（create-vue 官方形态）：vue essential + typescript-eslint recommended，
// 末尾挂 prettier 兼容层（关闭与格式化冲突的规则）。dist 与自动生成的 d.ts 不检查。
import { globalIgnores } from 'eslint/config'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'

export default defineConfigWithVueTs(
  {
    name: 'app/files-to-lint',
    files: ['**/*.{js,mjs,ts,mts,tsx,vue}'],
  },
  globalIgnores(['dist/**', 'node_modules/**', 'src/auto-imports.d.ts', 'src/components.d.ts']),
  pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,
  skipFormatting,
)
