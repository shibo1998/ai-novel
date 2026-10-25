<script setup lang="ts">
// 书架页（规格04 §1）：新建向导 + 书列表，删除二次确认。
import { ElMessage, ElMessageBox } from "element-plus";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { createBook, deleteBook, listBooks } from "@/api";
import type { BookCard } from "@/api/types";
import { useBookStore } from "@/stores/book";

const router = useRouter();
const store = useBookStore();
const books = ref<BookCard[]>([]);
const genres = [
  { value: "urban", label: "都市" },
  { value: "xuanhuan", label: "玄幻" },
  { value: "romance", label: "言情" },
  { value: "suspense", label: "悬疑" },
];
const form = ref({
  name: "",
  genre_template: "urban",
  target_words: 1_000_000,
  author: "",
  platform: "番茄",
});

async function load(): Promise<void> {
  books.value = await listBooks();
}
onMounted(() => void load().catch(() => {}));

function open(name: string): void {
  store.setCurrentBook(name);
  router.push(`/book/${encodeURIComponent(name)}`);
}

async function create(): Promise<void> {
  const name = form.value.name.trim();
  if (!name) {
    ElMessage.warning("书名必填");
    return;
  }
  try {
    await createBook({ ...form.value, name });
    ElMessage.success("已创建");
    form.value.name = "";
    await load();
    open(name);
  } catch {
    // 拦截器已提示
  }
}

async function del(name: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除《${name}》将删除 books/${name}/ 整个目录（含全部章节与档案），不可恢复。确认？`,
      "删除书",
      { type: "warning" },
    );
    await ElMessageBox.confirm(`再次确认：真的删除《${name}》？`, "删除书", { type: "warning" });
  } catch {
    return; // 用户取消
  }
  try {
    await deleteBook(name);
    ElMessage.success("已删除");
    if (store.currentBook === name) store.clearBook();
    await load();
  } catch {
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="panel">
    <h3 class="panel-title">新建书</h3>
    <el-form inline label-width="auto">
      <el-form-item label="书名">
        <el-input v-model="form.name" placeholder="书名" style="width: 160px" @keyup.enter="create" />
      </el-form-item>
      <el-form-item label="题材">
        <el-select v-model="form.genre_template" style="width: 100px">
          <el-option v-for="g in genres" :key="g.value" :label="g.label" :value="g.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="目标字数">
        <el-input-number v-model="form.target_words" :min="100000" :step="100000" style="width: 140px" />
      </el-form-item>
      <el-form-item label="笔名">
        <el-input v-model="form.author" placeholder="可空" style="width: 110px" />
      </el-form-item>
      <el-form-item label="平台">
        <el-input v-model="form.platform" style="width: 90px" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="create">创建</el-button>
      </el-form-item>
    </el-form>
  </div>

  <h3 class="panel-title">书列表</h3>
  <div v-if="books.length" class="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
    <div v-for="b in books" :key="b.name" class="panel mb-0">
      <div class="font-bold">{{ b.title || b.name }}</div>
      <div class="dim mt-1">
        {{ b.genre }} · {{ ((b.target_words ?? 0) / 10000).toFixed(0) }}万字 ·
        {{ b.platform }} · {{ b.status }}
      </div>
      <div class="mt-2 flex gap-2">
        <el-button type="primary" @click="open(b.name)">打开</el-button>
        <el-button type="danger" plain @click="del(b.name)">删除</el-button>
      </div>
    </div>
  </div>
  <div v-else class="dim">（空，先建书）</div>
</template>
