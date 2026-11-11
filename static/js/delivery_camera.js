/* Camera-only delivery evidence: intentionally no file input or gallery picker. */
(() => {
  let active = false;
  let cameraQueue = Promise.resolve();
  const requestCamera = () => {
    const request = cameraQueue.then(async () => {
      for (let attempt = 0; ; attempt++) {
        try {
          return await navigator.mediaDevices.getUserMedia({video: attempt === 0 ? {facingMode: {ideal: 'environment'}, width: {ideal: 1600}, height: {ideal: 1200}} : true, audio: false});
        } catch (error) {
          // Some phones briefly report a busy camera just after closing it.
          if (attempt >= 2 || !['NotReadableError', 'AbortError', 'NotFoundError'].includes(error.name)) throw error;
          await new Promise(resolve => setTimeout(resolve, 300 * (attempt + 1)));
        }
      }
    });
    cameraQueue = request.then(() => undefined, () => undefined);
    return request;
  };
  window.DeliveryCamera = {
    capture() {
      if (active) return Promise.resolve(null);
      active = true;
      return new Promise(resolve => {
        const modal = document.createElement('dialog');
        modal.setAttribute('aria-label', 'Delivery camera');
        modal.style.cssText = 'box-sizing:border-box;border:0;border-radius:18px;padding:22px;width:min(94vw,560px);max-height:95dvh;overflow:auto;color:#172c42;background:#fff;box-shadow:0 20px 80px #0006';
        modal.innerHTML = `
          <form method="dialog" style="margin:0">
            <h2 style="font-size:22px;margin:0 0 8px">Take delivery photo</h2>
            <p>Photograph the customer with the item, or the item being handed to the customer. A camera photo is required to mark this product delivered.</p>
            <video aria-label="Live camera" autoplay playsinline muted style="width:100%;max-height:48dvh;background:#101827;border-radius:10px;object-fit:contain"></video>
            <img alt="Delivery photo preview" hidden style="width:100%;max-height:48dvh;border-radius:10px;object-fit:contain">
            <p role="status" style="font-size:14px">Opening camera…</p>
            <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:16px">
              <button type="button" data-capture disabled>Take photo</button>
              <button type="button" data-retake hidden>Retake photo</button>
              <button type="button" data-use hidden>Use photo &amp; mark delivered</button>
              <button type="button" data-cancel>Cancel</button>
            </div>
          </form>`;
        modal.querySelectorAll('button').forEach(button => {
          button.style.cssText = 'padding:10px 14px;border-radius:8px;border:1px solid #cbd5e1;cursor:pointer;background:#f1f5f9;color:#172c42;font-weight:600';
        });
        const video = modal.querySelector('video');
        video.muted = true;
        const preview = modal.querySelector('img');
        const status = modal.querySelector('[role=status]');
        const capture = modal.querySelector('[data-capture]');
        const retake = modal.querySelector('[data-retake]');
        const use = modal.querySelector('[data-use]');
        let stream, blob, previewURL, finished = false;
        const stop = () => {
          stream?.getTracks().forEach(track => track.stop());
          stream = null;
          video.srcObject = null;
        };
        const finish = result => {
          if (finished) return;
          finished = true;
          stop();
          if (previewURL) URL.revokeObjectURL(previewURL);
          modal.close();
          modal.remove();
          active = false;
          resolve(result);
        };
        const openCamera = async () => {
          blob = null;
          capture.disabled = true;
          capture.hidden = false;
          use.hidden = retake.hidden = true;
          preview.hidden = true;
          video.hidden = false;
          if (previewURL) { URL.revokeObjectURL(previewURL); previewURL = null; }
          status.textContent = 'Opening camera…';
          try {
            if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera access requires HTTPS and a supported browser.');
            // Keep the stream during preview so retaking does not race a
            // mobile camera that is still shutting down. Finish stops it.
            const opened = stream || await requestCamera();
            if (finished) { opened.getTracks().forEach(track => track.stop()); return; }
            stream = opened;
            if (video.srcObject !== stream) video.srcObject = stream;
            await video.play();
            if (finished) return;
            capture.disabled = false;
            status.textContent = 'Position the customer and item in the frame, then take the photo.';
          } catch (error) {
            stop();
            if (finished) return;
            status.textContent = error.name === 'NotAllowedError'
              ? 'Camera permission was denied. Allow camera access in your browser and try again. Delivery cannot be confirmed without a photo.'
              : 'Unable to open the camera. Check camera availability and use HTTPS, then try again.';
            retake.hidden = false;
            retake.textContent = 'Try camera again';
          }
        };
        capture.onclick = () => {
          if (capture.disabled || !video.videoWidth || !video.videoHeight) return;
          capture.disabled = true;
          const canvas = document.createElement('canvas');
          const scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
          canvas.width = Math.round(video.videoWidth * scale);
          canvas.height = Math.round(video.videoHeight * scale);
          canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
          canvas.toBlob(snapshot => {
            if (finished) return;
            if (!snapshot) { capture.disabled = false; status.textContent = 'Photo capture failed. Please try again.'; return; }
            blob = snapshot;
            previewURL = URL.createObjectURL(blob);
            preview.src = previewURL;
            preview.hidden = false;
            video.hidden = true;
            capture.hidden = true;
            use.hidden = retake.hidden = false;
            retake.textContent = 'Retake photo';
            status.textContent = 'Check that the customer and item are clearly visible. Confirming uploads this photo and marks the product delivered once Packaging stock deductions are settled.';
            video.pause();
          }, 'image/jpeg', 0.85);
        };
        retake.onclick = openCamera;
        use.onclick = () => { if (blob) finish(blob); };
        modal.querySelector('[data-cancel]').onclick = () => finish(null);
        modal.addEventListener('cancel', event => { event.preventDefault(); finish(null); });
        modal.querySelector('form').onsubmit = event => event.preventDefault();
        document.body.append(modal);
        modal.showModal();
        void openCamera();
      });
    },
  };
})();
