<template>
  <div class="empty-state">
    <div class="illustration">
      <!-- 空状态插画 -->
      <svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" class="illustration-svg">
        <circle cx="60" cy="52" r="46" fill="#F0F6FF" />
        <circle cx="60" cy="52" r="34" fill="#E3EFFF" />
        <!-- 文档 -->
        <rect x="44" y="28" width="32" height="44" rx="5" fill="#409EFF" opacity="0.75" />
        <rect x="50" y="36" width="20" height="2.5" rx="1.25" fill="#fff" opacity="0.85" />
        <rect x="50" y="42" width="20" height="2.5" rx="1.25" fill="#fff" opacity="0.6" />
        <rect x="50" y="48" width="20" height="2.5" rx="1.25" fill="#fff" opacity="0.45" />
        <!-- 放大镜 -->
        <circle cx="72" cy="58" r="12" fill="none" stroke="#409EFF" stroke-width="3" />
        <line x1="81" y1="67" x2="88" y2="74" stroke="#409EFF" stroke-width="3" stroke-linecap="round" />
        <!-- 星星 -->
        <path d="M32 74 l2.2 4.5 5 .7 -3.6 3.5 .9 5 -4.5 -2.4 -4.5 2.4 .9 -5 -3.6-3.5 5-.7z" fill="#FFD666" />
        <path d="M88 24 l1.4 2.9 3.2 .4 -2.3 2.2 .6 3.2 -2.9 -1.5 -2.9 1.5 .6 -3.2 -2.3 -2.2 3.2-.4z" fill="#FFB448" />
      </svg>
    </div>
    <div class="title">{{ title }}</div>
    <div class="description" v-if="description">{{ description }}</div>
    <slot name="action"></slot>
  </div>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    default: '暂无内容',
  },
  description: {
    type: String,
    default: '',
  },
})
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 20px;
  text-align: center;
}

.illustration {
  margin-bottom: 20px;
}

.illustration-svg {
  width: 140px;
  height: 140px;
}

.title {
  font-size: 16px;
  font-weight: 500;
  color: #606266;
  margin-bottom: 8px;
}

.description {
  font-size: 13px;
  color: #909399;
  max-width: 320px;
  line-height: 1.6;
  margin-bottom: 16px;
}
</style>
