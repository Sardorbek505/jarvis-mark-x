// JARVIS Media Bridge — Content Script
(function () {
  'use strict';

  let currentMedia = null;

  function findMediaElement() {
    // Провайдер-специфичные селекторы
    if (window.location.hostname.includes('youtube.com')) {
      const ytVideo = document.querySelector('video.html5-main-video') || document.querySelector('video');
      if (ytVideo) return ytVideo;
    }
    if (window.location.hostname.includes('vk.com')) {
      const vkVideo = document.querySelector('video') || document.querySelector('.videoplayer_media');
      if (vkVideo) return vkVideo;
    }
    if (window.location.hostname.includes('kinopoisk.ru')) {
      const kpVideo = document.querySelector('video');
      if (kpVideo) return kpVideo;
    }
    return document.querySelector('video') || document.querySelector('audio');
  }

  function getMediaState(media) {
    if (!media) return null;
    return {
      paused: media.paused,
      currentTime: media.currentTime || 0.0,
      duration: media.duration || 0.0,
      volume: Math.round((media.volume || 0.0) * 100),
      muted: media.muted || false,
      playbackRate: media.playbackRate || 1.0,
      ended: media.ended || false,
      title: document.title || '',
      url: window.location.href
    };
  }

  function sendStateUpdate(reason) {
    const media = findMediaElement();
    if (!media) return;
    const state = getMediaState(media);
    if (state) {
      chrome.runtime.sendMessage({
        type: 'MEDIA_STATE_UPDATE',
        reason: reason,
        state: state
      });
    }
  }

  function attachListeners(media) {
    if (!media || media._jarvisAttached) return;
    media._jarvisAttached = true;
    currentMedia = media;

    const events = ['play', 'pause', 'timeupdate', 'volumechange', 'durationchange', 'ended', 'ratechange'];
    events.forEach(evt => {
      media.addEventListener(evt, () => sendStateUpdate(evt));
    });

    sendStateUpdate('attached');
  }

  // Наблюдатель за появлением медиа-элементов в DOM
  const observer = new MutationObserver(() => {
    const media = findMediaElement();
    if (media && media !== currentMedia) {
      attachListeners(media);
    }
  });

  observer.observe(document.documentElement, { childList: true, subtree: true });

  const initialMedia = findMediaElement();
  if (initialMedia) {
    attachListeners(initialMedia);
  }

  // Обработка команд от JARVIS через background.js
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    const media = findMediaElement();
    if (!media) {
      sendResponse({ success: false, error: 'NO_MEDIA_ELEMENT' });
      return true;
    }

    try {
      let resultMessage = 'OK';
      switch (request.command) {
        case 'play':
          media.play();
          resultMessage = 'Playing resumed';
          break;
        case 'pause':
          media.pause();
          resultMessage = 'Paused';
          break;
        case 'get_state':
          sendResponse({ success: true, state: getMediaState(media) });
          return true;
        case 'seek_absolute':
          if (typeof request.seconds === 'number') {
            media.currentTime = Math.max(0, Math.min(media.duration || 999999, request.seconds));
            resultMessage = `Seeked to ${request.seconds}s`;
          }
          break;
        case 'seek_relative':
          if (typeof request.seconds === 'number') {
            media.currentTime = Math.max(0, Math.min(media.duration || 999999, media.currentTime + request.seconds));
            resultMessage = `Seeked relative ${request.seconds}s`;
          }
          break;
        case 'seek_percent':
          if (typeof request.percent === 'number' && media.duration > 0) {
            const p = Math.max(0, Math.min(100, request.percent)) / 100.0;
            media.currentTime = media.duration * p;
            resultMessage = `Seeked to ${request.percent}%`;
          }
          break;
        case 'set_volume':
          if (typeof request.percent === 'number') {
            media.volume = Math.max(0, Math.min(100, request.percent)) / 100.0;
            resultMessage = `Volume set to ${request.percent}%`;
          }
          break;
        case 'mute':
          media.muted = true;
          resultMessage = 'Muted';
          break;
        case 'unmute':
          media.muted = false;
          resultMessage = 'Unmuted';
          break;
        case 'set_playback_rate':
          if (typeof request.rate === 'number') {
            media.playbackRate = request.rate;
            resultMessage = `Playback rate set to ${request.rate}`;
          }
          break;
        default:
          sendResponse({ success: false, error: 'UNKNOWN_COMMAND' });
          return true;
      }

      sendStateUpdate(request.command);
      sendResponse({ success: true, message: resultMessage, state: getMediaState(media) });
    } catch (err) {
      sendResponse({ success: false, error: err.toString() });
    }

    return true;
  });
})();
