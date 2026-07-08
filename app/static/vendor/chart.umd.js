/* Local, offline Chart.js-compatible fallback.
   It implements the small bar-chart subset used by this app. The file can be
   replaced with the official Chart.js UMD bundle without changing app.js. */
(function () {
  class Chart {
    constructor(canvas, config) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.config = config || {};
      this.draw();
    }

    destroy() {
      if (!this.ctx) return;
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }

    draw() {
      const ctx = this.ctx;
      const labels = this.config.data?.labels || [];
      const dataset = (this.config.data?.datasets || [])[0] || { data: [] };
      const data = dataset.data || [];
      const width = this.canvas.clientWidth || 720;
      const height = this.canvas.clientHeight || 300;
      const ratio = window.devicePixelRatio || 1;
      this.canvas.width = width * ratio;
      this.canvas.height = height * ratio;
      ctx.scale(ratio, ratio);
      ctx.clearRect(0, 0, width, height);
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--muted") || "#526070";
      ctx.fillText(dataset.label || "", 12, 18);
      const pad = { left: 42, right: 16, top: 32, bottom: 36 };
      const chartWidth = width - pad.left - pad.right;
      const chartHeight = height - pad.top - pad.bottom;
      const maxAbs = Math.max(60, ...data.map(value => Math.abs(Number(value) || 0)));
      const zeroY = pad.top + chartHeight / 2;
      ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue("--border") || "#d8e1eb";
      ctx.beginPath();
      ctx.moveTo(pad.left, zeroY);
      ctx.lineTo(width - pad.right, zeroY);
      ctx.stroke();
      const barGap = 6;
      const barWidth = Math.max(8, chartWidth / Math.max(1, data.length) - barGap);
      data.forEach((raw, index) => {
        const value = Number(raw) || 0;
        const x = pad.left + index * (barWidth + barGap);
        const barHeight = Math.abs(value) / maxAbs * (chartHeight / 2 - 8);
        const y = value >= 0 ? zeroY - barHeight : zeroY;
        ctx.fillStyle = value >= 0 ? "#15803d" : "#b91c1c";
        ctx.fillRect(x, y, barWidth, barHeight);
        ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--muted") || "#526070";
        ctx.save();
        ctx.translate(x + barWidth / 2, height - 8);
        ctx.rotate(-Math.PI / 5);
        ctx.textAlign = "right";
        ctx.fillText(labels[index] || "", 0, 0);
        ctx.restore();
      });
    }
  }

  window.Chart = Chart;
})();

