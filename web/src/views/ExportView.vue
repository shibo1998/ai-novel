<script setup lang="ts">
// 导出页（规格04 §1）：章节合并导出 txt / md / 全书打包 zip（后端 POST，blob 下载）。
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'

import { exportMd, exportTxt, exportZip, listBooks } from '@/api'
import type { BookCard } from '@/api/types'

const books = ref<BookCard[]>([])
const selected = ref('')

onMounted(() => {
  void listBooks()
    .then((bs) => {
      books.value = bs
      if (!selected.value && bs.length) selected.value = bs[0]!.name
    })
    .catch(() => {})
})

type ExportKind = 'txt' | 'md' | 'zip'

async function download(kind: ExportKind): Promise<void> {
  if (!selected.value) {
    ElMessage.warning('先选书')
    return
  }
  try {
    if (kind === 'txt') await exportTxt(selected.value)
    else if (kind === 'md') await exportMd(selected.value)
    else await exportZip(selected.value)
    ElMessage.success('已开始下载')
  } catch {
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="panel">
    <h3 class="panel-title">导出</h3>
    <el-form inline>
      <el-form-item label="书">
        <el-select v-model="selected" style="width: 260px" placeholder="选择书">
          <el-option v-for="b in books" :key="b.name" :label="b.title || b.name" :value="b.name" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button @click="download('txt')">导出定稿合并 txt</el-button>
        <el-button @click="download('md')">导出定稿合并 md</el-button>
        <el-button @click="download('zip')">全书打包 zip</el-button>
      </el-form-item>
    </el-form>
    <div class="dim">
      txt/md 只含 status=final 的定稿章节（md 用 <span class="mono">## 第N章</span> 二级标题）；zip
      打包 books/书名/ 整个目录。
    </div>
  </div>
</template>
