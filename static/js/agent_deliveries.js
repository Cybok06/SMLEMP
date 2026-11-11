(() => {
  const feedback = document.querySelector('[data-delivery-feedback]');
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-confirm-delivery]');
    if (!button || button.disabled) return;
    button.disabled = true;
    const original = button.textContent;
    try {
      const photo = await window.DeliveryCamera.capture();
      if (!photo) return;
      button.textContent = 'Uploading photo…';
      const body = new FormData();
      body.set('delivery_photo', photo, 'delivery-camera.jpg');
      const response = await fetch(button.dataset.confirmDelivery, {
        method: 'POST', body, credentials: 'same-origin',
        headers: {Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.message || 'Delivery could not be confirmed. Please try again.');
      const status = button.closest('[data-delivery-row]').querySelector('[data-delivery-status]');
      status.textContent = 'Delivered';
      status.className = 'delivery-status delivered';
      button.remove();
      feedback.textContent = data.message || 'Product marked Delivered.';
      feedback.className = 'delivery-feedback success';
      feedback.hidden = false;
      window.dispatchEvent(new Event('agent-delivery-updated'));
      // Reload the filtered queue and its pagination after the successful write.
      window.setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      feedback.textContent = error.message || 'Delivery could not be confirmed.';
      feedback.className = 'delivery-feedback';
      feedback.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });
})();
