<script setup lang="ts">
// 章节工作台（规格04 §2 核心页）：细纲 → 流式生成 → 机检 → 审校 → 人工关口/定稿 + 装配预览。
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import {
  backToOutline,
  confirmOutline,
  finalizeChapter,
  generateOutlineG6,
  getAssemblyPreview,
  getOutlineRaw,
  getWorkbench,
  listVersions,
  putChapterOutline,
  rewriteFrom,
  runExempt,
  runFix,
  runMechcheck,
  runPolish,
  runReview,
} from "@/api";
import type { PreviewResult, VersionInfo, WorkbenchData } from "@/api/types";
import { useBookStore } from "@/stores/book";
import { tagType } from "@/utils/status";

const route = useRoute();
const router = useRouter();
const store = useBookStore();

const book = computed(() => String(route.params.name ?? ""));
const no = computed(() => Number(route.params.no ?? 1) || 1);
const data = ref<WorkbenchData | null>(null);
const versions = ref<VersionInfo[]>([]);
const rewriteVer = ref<number | null>(null);

const promptOptions = [
  { value: "", label: "自动（卷首三章W3 / 其余W1）" },
  { value: "W2", label: "W2 对话专场" },
  { value: "W4", label: "W4 突破场景" },
  { value: "W5", label: "W5 高潮场景" },
];
const promptId = ref("");
const generating = ref(false);
const streamText = ref("（生成区）");
const streamBox = ref<HTMLElement | null>(null);

const rawDialog = ref(false);
const rawText = ref("");
const preview = ref<PreviewResult | null>(null);

const outlineEditable = computed(() =>
  ["awaiting_outline", "outline_draft", "error"].includes(data.value?.status ?? ""),
);

const REVIEW_DIMS: [string, string][] = [
  ["ai_flavor", "AI味"],
  ["plot", "剧情一致性"],
  ["pacing", "节奏爽点"],
  ["prose", "文笔画面"],
  ["hook", "开篇钩子"],
];

async function load(): Promise<void> {
  store.setCurrentBook(book.value);
  data.value = await getWorkbench(book.value, no.value);
  streamText.value = data.value.prose?._body ?? "（生成区）";
  await loadVersions();
}
watch([book, no], () => void load().catch(() => {}), { immediate: true });

watch(streamText, () => {
  void nextTick(() => {
    if (streamBox.value) streamBox.value.scrollTop = streamBox.value.scrollHeight;
  });
});

async function reload(): Promise<void> {
  try {
    await load();
  } catch {
    // 拦截器已提示
  }
}

function nav(d: number): void {
  router.push(`/book/${encodeURIComponent(book.value)}/chapter/${Math.max(1, no.value + d)}`);
}

// —— ① 细纲（章合同） ——
async function genOutline(): Promise<void> {
  try {
    await generateOutlineG6(book.value, no.value);
    ElMessage.success("细纲已生成（未确认）");
    await reload();
  } catch {
    // 拦截器已提示
  }
}

async function confirmOutlineAct(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      "确认细纲后将锁定，正文阶段不再接受细纲变更。确认？",
      "确认细纲",
      { type: "warning" },
    );
  } catch {
    return;
  }
  try {
    await confirmOutline(book.value, no.value);
    ElMessage.success("已锁定");
    await reload();
  } catch {
    // 拦截器已提示
  }
}

async function editRaw(): Promise<void> {
  try {
    rawText.value = (await getOutlineRaw(book.value, no.value)).content;
    rawDialog.value = true;
  } catch {
    // 拦截器已提示
  }
}

async function saveRaw(): Promise<void> {
  const m = rawText.value.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) {
    ElMessage.error("格式：--- frontmatter --- 正文");
    return;
  }
  try {
    await putChapterOutline(book.value, no.value, { fm_raw: m[1]!, body: m[2]! });
    ElMessage.success("细纲已保存");
    rawDialog.value = false;
    await reload();
  } catch {
    // 拦截器已提示
  }
}

async function saveField(key: string, value: string): Promise<void> {
  const d = data.value;
  if (!d?.outline) return;
  const fm: Record<string, unknown> = { ...d.outline };
  delete fm._body;
  fm[key] = value;
  try {
    await putChapterOutline(book.value, no.value, { fm, body: d.outline._body ?? "" });
    ElMessage.success(`已改 ${key} = ${value}`);
  } catch {
    // 拦截器已提示
  }
  await reload();
}

async function saveHook(type: string): Promise<void> {
  const d = data.value;
  if (!d?.outline) return;
  const fm: Record<string, unknown> = { ...d.outline };
  delete fm._body;
  fm.hook = { ...(d.outline.hook ?? {}), type };
  try {
    await putChapterOutline(book.value, no.value, { fm, body: d.outline._body ?? "" });
    ElMessage.success(`钩子类型 → ${type}`);
  } catch {
    // 拦截器已提示
  }
  await reload();
}

// —— ② 正文生成（SSE：fetch 流式读取；协议 data: {json}\n\n，event: done/error） ——
async function generate(): Promise<void> {
  generating.value = true;
  streamText.value = "";
  try {
    const res = await fetch(
      `/api/books/${encodeURIComponent(book.value)}/chapters/${no.value}/generate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(promptId.value ? { prompt_id: promptId.value } : {}),
      },
    );
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let finished = false;
    while (!finished) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let evt = "message";
        let payload = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) evt = line.slice(7).trim();
          else if (line.startsWith("data: ")) payload += line.slice(6);
        }
        if (evt === "done") {
          ElMessage.success("生成完成");
          finished = true;
          break;
        }
        if (evt === "error") {
          ElMessage.error(`生成失败: ${payload}`);
          finished = true;
          break;
        }
        try {
          streamText.value += (JSON.parse(payload) as { text?: string }).text ?? "";
        } catch {
          // 忽略非 JSON 帧
        }
      }
    }
    await reload();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e));
  } finally {
    generating.value = false;
  }
}

// —— 装配预览 ——
async function loadPreview(): Promise<void> {
  try {
    preview.value = await getAssemblyPreview(book.value, no.value);
  } catch {
    // 拦截器已提示
  }
}

// —— ③ 机械检查 ——
async function mech(): Promise<void> {
  try {
    const r = await runMechcheck(book.value, no.value);
    ElMessage.success(`机械检查完成，状态 → ${r.status}`);
    await reload();
  } catch {
    // 拦截器已提示
  }
}

// —— ④ LLM 审校 ——
async function review(): Promise<void> {
  try {
    const r = await runReview(book.value, no.value);
    ElMessage.success(`审校 verdict=${r.verdict}，状态 → ${r.status}`);
    await reload();
  } catch {
    // 拦截器已提示
  }
}

// —— ⑤ 人工关口 / 定稿 ——
async function fix(): Promise<void> {
  try {
    const r = await runFix(book.value, no.value);
    ElMessage.success(`修复完成 ${r.word_count} 字`);
    await reload();
  } catch {
    // 拦截器已提示
  }
}

async function polish(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      "整章润色（V2 去AI味 + V3 复核）：不改剧情人设，字数浮动>10%或机检阻断会自动丢弃。继续？",
      "整章润色",
      { type: "warning" },
    );
  } catch {
    return;
  }
  try {
    const r = await runPolish(book.value, no.value);
    if (r.ok) {
      ElMessage.success({
        message: `润色完成：${r.word_count?.before}→${r.word_count?.after} 字，V3=${r.v3?.verdict}，改动 ${r.changes?.length ?? 0} 处`,
        duration: 8000,
      });
    } else {
      ElMessage.warning(`润色被丢弃：${r.reason}`);
    }
    await reload();
  } catch {
    // 拦截器已提示
  }
}

async function exempt(): Promise<void> {
  let reason = "";
  try {
    const r = await ElMessageBox.prompt(
      "豁免理由（必填，将写入 generation_log 作为合规证据）",
      "豁免",
      { inputValidator: (v: string) => (v?.trim() ? true : "理由必填") },
    );
    reason = r.value ?? "";
  } catch {
    return;
  }
  try {
    await runExempt(book.value, no.value, reason);
    ElMessage.success("已豁免 → 待定稿");
    await reload();
  } catch {
    // 拦截器已提示
  }
}

async function backOutline(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      "回到 outline_draft 修改细纲？（正文保留，改完需重新确认+生成）",
      "改细纲重写",
      { type: "warning" },
    );
  } catch {
    return;
  }
  try {
    await backToOutline(book.value, no.value);
    ElMessage.success("已回到 outline_draft");
    await reload();
  } catch {
    // 拦截器已提示
  }
}

async function finalize(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      "第一次确认：定稿将写快照、置 status=final 并触发记忆卡回写。继续？",
      "定稿",
      { type: "warning" },
    );
    await ElMessageBox.confirm("第二次确认：真的定稿？", "定稿", { type: "warning" });
  } catch {
    return;
  }
  try {
    const r = await finalizeChapter(book.value, no.value);
    ElMessage.success({
      message: `已定稿，快照 v${r.snapshot_version}；回写：${JSON.stringify(r.writeback).slice(0, 200)}`,
      duration: 10000,
    });
    await reload();
    void store.refreshConflicts();
  } catch {
    // 拦截器已提示
  }
}

async function loadVersions(): Promise<void> {
  try {
    versions.value = await listVersions(book.value, no.value);
    rewriteVer.value = versions.value.length
      ? versions.value[versions.value.length - 1]!.version
      : null;
  } catch {
    // 无快照时静默
  }
}

async function rewrite(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `从 v${rewriteVer.value ?? "当前"} 重写？将开新版本分支，旧版本保留。`,
      "从版本重写",
      { type: "warning" },
    );
  } catch {
    return;
  }
  try {
    const r = await rewriteFrom(book.value, no.value, rewriteVer.value);
    ElMessage.success(`新分支 v${r.new_version}`);
    await reload();
  } catch {
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="panel flex flex-wrap items-center gap-2">
    <el-button @click="nav(-1)">← 上一章</el-button>
    <span class="font-bold">第 {{ no }} 章</span>
    <el-button @click="nav(1)">下一章 →</el-button>
    <span>
      状态
      <el-tag :type="tagType(data?.status ?? '')" size="small">{{ data?.status ?? "…" }}</el-tag>
    </span>
    <span class="dim">版本 {{ data?.row ? "v" + data.row.current_version : "-" }}</span>
    <span class="dim">本章成本 {{ data?.cost ? (data.cost.cost_cents / 100).toFixed(2) + "元" : "0元" }}</span>
    <el-tag v-if="data?.golden3" type="warning" size="small">黄金三章专项</el-tag>
    <span class="flex-1"></span>
    <el-button @click="reload">刷新</el-button>
  </div>

  <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">
    <div>
      <!-- ① 细纲 -->
      <div class="panel">
        <h3 class="panel-title">① 细纲（章合同）</h3>
        <div class="mb-2 flex flex-wrap gap-2">
          <el-button type="primary" @click="genOutline">生成细纲（P-G6）</el-button>
          <el-button @click="confirmOutlineAct">确认细纲（锁定）</el-button>
          <el-button @click="editRaw">编辑细纲原文</el-button>
        </div>
        <template v-if="data?.outline">
          <div class="text-xs"><b>核心事件：</b>{{ data.outline.core_event }}</div>
          <div class="my-1 flex flex-wrap items-center gap-2 text-xs">
            <span><b>stage</b></span>
            <el-select
              :model-value="data.outline.stage"
              :disabled="!outlineEditable"
              style="width: 90px"
              @change="(v: string) => saveField('stage', v)"
            >
              <el-option v-for="s in ['引入', '发展', '转折', '高潮', '收束']" :key="s" :label="s" :value="s" />
            </el-select>
            <span><b>情绪</b></span>
            <el-select
              :model-value="data.outline.emotion_task"
              :disabled="!outlineEditable"
              style="width: 80px"
              @change="(v: string) => saveField('emotion_task', v)"
            >
              <el-option v-for="s in ['压', '小扬', '爆']" :key="s" :label="s" :value="s" />
            </el-select>
            <span><b>pov</b> {{ data.outline.pov }}</span>
            <span><b>钩子类型</b></span>
            <el-select
              :model-value="data.outline.hook?.type"
              :disabled="!outlineEditable"
              style="width: 80px"
              @change="(v: string) => saveHook(v)"
            >
              <el-option v-for="s in ['悬念', '选择', '反转']" :key="s" :label="s" :value="s" />
            </el-select>
            <span class="dim">{{ data.outline.hook?.content }}</span>
          </div>
          <div class="text-xs">
            <b>伏笔操作：</b><span class="mono">{{ JSON.stringify(data.outline.foreshadow_ops ?? []) }}</span>
          </div>
          <div class="mt-1 text-xs">
            <b>确认锁：</b>{{ data.outline.confirmed ? "✅ 已锁定（改字段请先「改细纲重写」）" : "未确认" }}
          </div>
          <pre class="stream-box mt-2" style="max-height: 260px">{{ data.outline._body }}</pre>
        </template>
        <span v-else class="dim">（未生成细纲）</span>
      </div>

      <!-- ② 正文生成 -->
      <div class="panel">
        <h3 class="panel-title">② 正文生成</h3>
        <div class="flex flex-wrap items-center gap-2">
          <el-button type="primary" :disabled="generating" @click="generate">
            {{ generating ? "生成中…" : "开始生成（流式）" }}
          </el-button>
          <el-select v-model="promptId" style="width: 230px">
            <el-option v-for="o in promptOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
        <pre ref="streamBox" class="stream-box mt-2">{{ streamText }}</pre>
      </div>

      <!-- 装配预览 -->
      <div class="panel">
        <h3 class="panel-title">装配预览（13 槽位 · 排查跑偏第一入口）</h3>
        <el-button @click="loadPreview">生成装配预览</el-button>
        <template v-if="preview">
          <div class="dim mt-2">
            总估算 {{ preview.total_est_tokens }} tokens / 预算 {{ preview.budget_tokens }} ·
            已存 {{ preview.saved_to || "" }}
          </div>
          <el-collapse class="mt-1">
            <el-collapse-item
              v-for="(s, idx) in preview.slots"
              :key="idx"
              :title="`${s.title} · ${s.est_tokens}t${s.truncated ? ' · 已截断' : ''}`"
            >
              <pre class="stream-box">{{ s.content || "（空）" }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </div>

    <div>
      <!-- ③ 机械检查 -->
      <div class="panel">
        <h3 class="panel-title">③ 机械检查（零 token）</h3>
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <el-button type="primary" @click="mech">跑机械检查（含自动修复1轮）</el-button>
          <span class="dim">阻断→自动 P-V5 修复一轮→重查；敏感词直接转人工</span>
        </div>
        <template v-if="data?.mech">
          <div class="mb-1">
            结论：<el-tag :type="tagType(data.mech.verdict)" size="small">{{ data.mech.verdict }}</el-tag>
            （{{ data.mech.issues.length }} 条）
          </div>
          <div
            v-for="(i, idx) in data.mech.issues"
            :key="idx"
            class="issue"
            :class="{ blocking: i.level === 'block' }"
          >
            <b>{{ i.rule }}</b>
            <el-tag :type="tagType(i.level)" size="small">{{ i.level }}</el-tag>
            行{{ i.line }} · {{ i.word }}<br />
            <span class="dim">{{ i.message }}</span>
          </div>
        </template>
        <span v-else class="dim">（未跑）</span>
      </div>

      <!-- ④ LLM 审校 -->
      <div class="panel">
        <h3 class="panel-title">④ LLM 审校（P-V1 五维）</h3>
        <el-button type="primary" @click="review">跑审校</el-button>
        <template v-if="data?.review">
          <div class="mb-1 mt-2">
            verdict：
            <el-tag
              :type="tagType(data.review.verdict === 'pass' ? 'pass' : data.review.verdict === 'fix' ? 'warn' : 'bad')"
              size="small"
            >
              {{ data.review.verdict }}
            </el-tag>
          </div>
          <div v-for="[k, t] in REVIEW_DIMS" :key="k" class="my-1 flex items-center gap-2">
            <span class="shrink-0 text-xs" style="width: 70px">{{ t }}</span>
            <el-progress
              :percentage="data.review?.scores?.[k] ?? 0"
              :stroke-width="8"
              class="flex-1"
            />
          </div>
          <div
            v-for="(i, idx) in data.review.issues ?? []"
            :key="idx"
            class="issue"
            :class="{ blocking: i.severity === 'blocking' }"
          >
            <b>{{ i.category }}</b>
            <el-tag :type="tagType(i.severity)" size="small">{{ i.severity }}</el-tag>
            {{ i.location }}<br />
            引用：<span class="mono">{{ i.quote }}</span><br />
            <span class="dim">{{ i.description }}</span><br />
            建议：{{ i.suggestion }}
          </div>
        </template>
        <span v-else class="dim">（未跑）</span>
      </div>

      <!-- ⑤ 人工关口 / 定稿 -->
      <div class="panel">
        <h3 class="panel-title">⑤ 人工关口 / 定稿</h3>
        <div class="mb-2 flex flex-wrap gap-2">
          <el-button @click="fix">定向修复（P-V5）</el-button>
          <el-button @click="polish">整章润色（P-V2+V3）</el-button>
          <el-button @click="exempt">豁免（必填理由）</el-button>
          <el-button @click="backOutline">改细纲重写</el-button>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <el-button type="primary" @click="finalize">定稿（二次确认→快照+回写）</el-button>
          <el-button type="danger" plain @click="rewrite">从版本重写</el-button>
          <el-select
            v-model="rewriteVer"
            placeholder="选择快照版本"
            clearable
            style="width: 260px"
          >
            <el-option
              v-for="v in versions"
              :key="v.version"
              :value="v.version"
              :label="`v${v.version} ${v.label} ${v.created_at.slice(0, 16)}`"
            />
          </el-select>
        </div>
        <div class="dim mt-2">
          {{ versions.length ? `快照 ${versions.length} 个（定稿历史全保留）` : "（暂无快照）" }}
        </div>
      </div>
    </div>
  </div>

  <!-- 细纲原文编辑 -->
  <el-dialog v-model="rawDialog" width="860px" top="4vh" destroy-on-close>
    <template #header><span class="font-bold">细纲原文（YAML frontmatter + 场景清单）</span></template>
    <el-input v-model="rawText" type="textarea" :rows="22" class="mono" />
    <template #footer>
      <el-button @click="rawDialog = false">取消</el-button>
      <el-button type="primary" @click="saveRaw">保存细纲</el-button>
    </template>
  </el-dialog>
</template>
