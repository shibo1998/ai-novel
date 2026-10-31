// 会话级导航缓存：只持久化书名/章号，绝不持久化未保存正文或候选（规格07 §0.4、UI-08）。
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { listConflicts } from '@/api'
import type { BookCard } from '@/api/types'

const CHAPTER_PREFIX = 'recentChapter:'
const RECENT_BOOKS = 'recentBooks'

function readRecentBooks(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_BOOKS) ?? '[]') as unknown
    return Array.isArray(value)
      ? value.filter((v): v is string => typeof v === 'string').slice(0, 3)
      : []
  } catch {
    return []
  }
}

export const useBookStore = defineStore('book', () => {
  const currentBook = ref(localStorage.getItem('currentBook') ?? '')
  const recentBooks = ref<string[]>(readRecentBooks())
  const conflictCount = ref(0)

  function setCurrentBook(name: string): void {
    if (!name) return
    currentBook.value = name
    localStorage.setItem('currentBook', name)
    recentBooks.value = [name, ...recentBooks.value.filter((b) => b !== name)].slice(0, 3)
    localStorage.setItem(RECENT_BOOKS, JSON.stringify(recentBooks.value))
  }

  function rememberChapter(book: string, no: number): void {
    if (Number.isInteger(no) && no > 0) {
      setCurrentBook(book)
      localStorage.setItem(`${CHAPTER_PREFIX}${book}`, String(no))
    }
  }

  function recentChapter(book: string): number | null {
    const no = Number(localStorage.getItem(`${CHAPTER_PREFIX}${book}`))
    return Number.isInteger(no) && no > 0 ? no : null
  }

  function reconcileBooks(books: BookCard[]): void {
    const valid = new Set(books.map((b) => b.name))
    recentBooks.value = recentBooks.value.filter((b) => valid.has(b))
    localStorage.setItem(RECENT_BOOKS, JSON.stringify(recentBooks.value))
    if (currentBook.value && !valid.has(currentBook.value)) clearBook()
  }

  function clearBook(): void {
    const old = currentBook.value
    currentBook.value = ''
    localStorage.removeItem('currentBook')
    if (old) localStorage.removeItem(`${CHAPTER_PREFIX}${old}`)
    recentBooks.value = recentBooks.value.filter((b) => b !== old)
    localStorage.setItem(RECENT_BOOKS, JSON.stringify(recentBooks.value))
  }

  async function refreshConflicts(): Promise<void> {
    try {
      conflictCount.value = (await listConflicts()).length
    } catch {
      // 统一错误模块负责聚合计数；轮询不逐次弹窗。
    }
  }

  return {
    currentBook,
    recentBooks,
    conflictCount,
    setCurrentBook,
    rememberChapter,
    recentChapter,
    reconcileBooks,
    clearBook,
    refreshConflicts,
  }
})
