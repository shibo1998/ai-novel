// axios 实例：统一错误提示（FastAPI {detail} 结构）。LLM 长调用（审校/审计/AI 生成）不设超时。
// 约定：拦截器负责弹错误 toast，调用方 catch 只做流程控制，不再重复提示。
import axios from "axios";
import { ElMessage } from "element-plus";

export const http = axios.create({ timeout: 0 });

http.interceptors.response.use(
  (res) => res,
  (err: unknown) => {
    let detail = "请求失败";
    if (axios.isAxiosError(err)) {
      const d = err.response?.data as { detail?: unknown } | undefined;
      detail = typeof d?.detail === "string" ? d.detail : err.message;
    } else if (err instanceof Error) {
      detail = err.message;
    }
    ElMessage.error({ message: String(detail), duration: 8000 });
    return Promise.reject(err);
  },
);
