<template>
  <div class="source-tree">
    <!-- 溯源标题 -->
    <div class="source-header">
      <el-icon><Document /></el-icon>
      <span>溯源来源</span>
      <span class="count">{{ sources.length }} 篇</span>
    </div>

    <!-- 溯源列表 -->
    <div class="source-list">
      <div
        v-for="(doc, docIndex) in sources"
        :key="docIndex"
        class="source-item"
      >
        <!-- 一级：文档 -->
        <div class="doc-level" @click="toggleDoc(docIndex)">
          <el-icon class="arrow" :class="{ expanded: doc.expanded }">
            <ArrowRight />
          </el-icon>
          <el-tag size="small" type="success">{{ doc.score != null ? Math.round(doc.score * 100) + '%' : '' }}</el-tag>
          <span class="doc-title">{{ doc.title || doc.name || '文档' }}</span>
        </div>

        <!-- 二级：章节 -->
        <el-collapse-transition>
          <div v-show="doc.expanded" class="chapter-level">
            <div
              v-for="(chapter, chapIndex) in doc.chapters"
              :key="chapIndex"
              class="chapter-item"
            >
              <div class="chapter-title" @click="toggleChapter(docIndex, chapIndex)">
                <el-icon class="arrow" :class="{ expanded: chapter.expanded }">
                  <ArrowRight />
                </el-icon>
                <span>{{ chapter.title || chapter.name || '章节' }}</span>
              </div>

              <!-- 三级：段落 -->
              <el-collapse-transition>
                <div v-show="chapter.expanded" class="para-level">
                  <div
                    v-for="(para, paraIndex) in chapter.paragraphs"
                    :key="paraIndex"
                    class="para-item"
                    @click="$emit('select', { doc, chapter, para })"
                  >
                    <p class="para-text">{{ para.content || para.text }}</p>
                    <div class="para-page" v-if="para.page">
                      <el-tag size="small" type="info">第{{ para.page }}页</el-tag>
                    </div>
                  </div>
                </div>
              </el-collapse-transition>
            </div>
          </div>
        </el-collapse-transition>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'

const props = defineProps({
  sources: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['select'])

// 展开状态管理（在props上维护展开状态副本）
const state = reactive({})

const initState = () => {
  props.sources.forEach((doc, di) => {
    if (!state[di]) state[di] = { docExpanded: true }
    if (doc.chapters) {
      doc.chapters.forEach((chap, ci) => {
        if (!state[di][ci]) state[di][ci] = { chapExpanded: false }
      })
    }
  })
}

watch(
  () => props.sources,
  () => initState(),
  { deep: true, immediate: true }
)

// 由于props.sources是只读的，使用内部引用同步展开
const toggleDoc = (docIndex) => {
  const doc = props.sources[docIndex]
  if (doc) doc.expanded = !doc.expanded
}

const toggleChapter = (docIndex, chapIndex) => {
  const doc = props.sources[docIndex]
  if (doc && doc.chapters) {
    const chap = doc.chapters[chapIndex]
    if (chap) chap.expanded = !chap.expanded
  }
}
</script>

<style scoped>
.source-tree {
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  margin-top: 12px;
  overflow: hidden;
}

.source-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 12px 16px;
  background: #ecf5ff;
  font-weight: 600;
  font-size: 14px;
  color: #409EFF;
}

.source-header .count {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
  font-weight: normal;
}

.source-list {
  padding: 8px 0;
}

.source-item {
  border-bottom: 1px solid #f5f7fa;
}

.source-item:last-child {
  border-bottom: none;
}

.doc-level {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  cursor: pointer;
  transition: background 0.2s;
}

.doc-level:hover {
  background: #f5f7fa;
}

.arrow {
  transition: transform 0.2s;
  font-size: 12px;
  color: #c0c4cc;
}

.arrow.expanded {
  transform: rotate(90deg);
}

.doc-title {
  font-weight: 500;
  font-size: 14px;
}

.chapter-level {
  padding-left: 32px;
  background: #fafbfc;
}

.chapter-item {
  border-top: 1px dashed #f0f2f5;
}

.chapter-title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  color: #606266;
  transition: background 0.2s;
}

.chapter-title:hover {
  background: #f0f2f5;
}

.para-level {
  padding-left: 32px;
  padding-right: 12px;
}

.para-item {
  padding: 8px 12px;
  margin: 4px 0;
  background: #fff;
  border: 1px solid #f0f2f5;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
}

.para-item:hover {
  border-color: #409EFF;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.12);
}

.para-text {
  margin: 0;
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.para-page {
  margin-top: 4px;
}
</style>
