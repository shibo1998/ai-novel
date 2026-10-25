// 会话级状态：当前书 + 裁决队列角标轮询。数据真相源始终是后端（规格04 §0：store 只作缓存）。
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { listConflicts } from '@/api'

export const useBookStore = defineStore('book', () => {
  const currentBook = ref(localStorage.getItem('currentBook') ?? '')
  const conflictCount = ref(0)

  function setCurrentBook(name: string): void {
    currentBook.value = name
    localStorage.setItem('currentBook', name)
  }

  function clearBook(): void {
    currentBook.value = ''
    localStorage.removeItem('currentBook')
  }

  async function refreshConflicts(): Promise<void> {
    try {
      conflictCount.value = (await listConflicts()).length
    } catch {
      // 轮询失败静默（后端未起时避免刷屏）
    }
  }

  return { currentBook, conflictCount, setCurrentBook, clearBook, refreshConflicts }
})
