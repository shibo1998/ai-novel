// Axios 统一入口（规格07 UI-24）：错误进入会话记录并保留永久通知；主动取消不记错误。
import axios from 'axios'
import { recordError } from '@/utils/errors'
import type { ErrorContext } from '@/utils/errors'

declare module 'axios' {
  interface AxiosRequestConfig {
    errorContext?: ErrorContext
  }
}

export const http = axios.create({ timeout: 30_000 })

http.interceptors.response.use(
  (res) => res,
  (err: unknown) => {
    if (axios.isCancel(err) || (axios.isAxiosError(err) && err.code === 'ERR_CANCELED')) {
      return Promise.reject(err)
    }
    const config = axios.isAxiosError(err)
      ? (err.config as
          | {
              errorContext?: {
                operation?: string
                book?: string
                chapter?: number
                background?: boolean
                write?: boolean
              }
            }
          | undefined)
      : undefined
    recordError(err, config?.errorContext)
    return Promise.reject(err)
  },
)
