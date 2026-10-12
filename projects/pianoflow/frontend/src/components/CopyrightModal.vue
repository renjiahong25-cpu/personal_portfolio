<script setup>
defineEmits(['accept'])
</script>

<template>
  <div class="modal-backdrop">
    <div class="modal">
      <h2>版权与使用声明</h2>
      <p>PianoFlow 是一个<strong>仅供个人学习与音乐研究</strong>的开源音乐工作台。</p>
      <ul>
        <li>下载的音频仅保存在本机缓存目录，不会上传到任何云端服务。</li>
        <li>禁止批量下载、二次分发、转载或商用，使用者自行承担版权责任。</li>
        <li>一次只能处理一首歌，下载间隔会被主动限速。</li>
        <li>AI 生成的乐谱与解析结果仅供参考，不能作为出版或演奏依据。</li>
      </ul>
      <div class="modal-actions">
        <button class="primary" @click="$emit('accept')">我已阅读并同意</button>
      </div>
    </div>
  </div>
</template>
