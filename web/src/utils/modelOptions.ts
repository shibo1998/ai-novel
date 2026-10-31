// 接口与模型清单的共享缓存（规格13 §3）：全站只拉一次，设置页改完可整体失效。
import { getSettings, listModels } from '@/api'

export const DEFAULT_PROVIDER = 'openai'

export interface ProviderOption {
  name: string
  label: string
}

let providerPromise: Promise<ProviderOption[]> | null = null
const modelCache = new Map<string, Promise<string[]>>()

export function getProviders(): Promise<ProviderOption[]> {
  providerPromise ??= getSettings()
    .then((s) => {
      const raw = s.providers ?? {}
      return Object.keys(raw)
        .sort()
        .map((name) => ({ name, label: raw[name]?.label || name }))
    })
    .catch(() => [])
  return providerPromise
}

/** 拉某个接口的模型清单；失败返回空数组（界面退回手填，不阻断）。 */
export function getModels(provider: string): Promise<string[]> {
  const key = provider || DEFAULT_PROVIDER
  let pending = modelCache.get(key)
  if (!pending) {
    pending = listModels(key)
      .then((r) => (r.ok ? r.models.map((m) => m.id) : []))
      .catch(() => [])
    modelCache.set(key, pending)
  }
  return pending
}

export function resetModelCaches(): void {
  providerPromise = null
  modelCache.clear()
}
