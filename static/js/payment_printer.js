/* Receipt encoder adapted from printer_test-main/index.html. */

(() => {

"use strict";

const PAPER_COLUMNS=32;

const state={receiptFormat:"short"};

const el=id=>document.getElementById(id);

const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

const money=value=>Number(value||0).toLocaleString("en-GH",{minimumFractionDigits:2,maximumFractionDigits:2});

      function sanitizePrinterText(value) {
        return String(value ?? "")
          .normalize("NFKD")
          .replace(/[₵¢]/g, "GHS ")
          .replace(/[–—]/g, "-")
          .replace(/[‘’]/g, "'")
          .replace(/[“”]/g, '"')
          .replace(/…/g, "...")
          .replace(/[^\x20-\x7E\n]/g, "");
      }

      function wrapText(value, width = PAPER_COLUMNS) {
        const clean = sanitizePrinterText(value).trim();
        if (!clean) return [""];
        const words = clean.split(/\s+/);
        const lines = [];
        let line = "";
        words.forEach((word) => {
          if (word.length > width) {
            if (line) { lines.push(line); line = ""; }
            for (let i = 0; i < word.length; i += width) lines.push(word.slice(i, i + width));
          } else if (!line) {
            line = word;
          } else if (`${line} ${word}`.length <= width) {
            line += ` ${word}`;
          } else {
            lines.push(line);
            line = word;
          }
        });
        if (line) lines.push(line);
        return lines;
      }

      function centered(value, width = PAPER_COLUMNS) {
        const text = sanitizePrinterText(value).slice(0, width);
        const left = Math.max(0, Math.floor((width - text.length) / 2));
        return `${" ".repeat(left)}${text}`;
      }

      function pairLine(label, value, width = PAPER_COLUMNS) {
        const left = sanitizePrinterText(label);
        const right = sanitizePrinterText(value);
        if (left.length + right.length + 1 <= width) {
          return `${left}${" ".repeat(width - left.length - right.length)}${right}`;
        }
        return `${left}\n${wrapText(right, width).map((line) => line.padStart(Math.min(width, line.length + 2))).join("\n")}`;
      }

      function getPaymentProgress(receipt) {
        const total = Math.max(0, Number(receipt.productTotal) || 0);
        const paid = Math.max(0, Number(receipt.totalPaid) || 0);
        const ratio = total > 0 ? Math.min(1, paid / total) : 0;
        // Do not label an almost-complete payment as 100%.
        const percent = Math.floor(ratio * 1000 + 1e-8) / 10;
        return { total, ratio, percent };
      }

      function createProgressCanvas(receipt) {
        const progress = getPaymentProgress(receipt);
        const canvas = document.createElement("canvas");
        canvas.width = 360;
        canvas.height = 66;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.fillStyle = "#000";
        context.font = "900 24px Arial, sans-serif";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(`${progress.percent}% PAID`, 180, 15);
        context.strokeStyle = "#000";
        context.lineWidth = 2;
        context.strokeRect(1, 35, 358, 26);
        const fillWidth = Math.round(352 * progress.ratio);
        if (fillWidth > 0) context.fillRect(4, 38, fillWidth, 20);
        return canvas;
      }

      function safeQrText(value, maxLength = 180) {
        return sanitizePrinterText(value).replace(/\s+/g, " ").trim().slice(0, maxLength);
      }

      function tryRenderQr(holder, payload, size) {
        if (!window.QRCode || !holder) return false;
        holder.replaceChildren();
        try {
          new QRCode(holder, {
            text: safeQrText(payload),
            width: size,
            height: size,
            colorDark: "#000000",
            colorLight: "#ffffff",
            correctLevel: QRCode.CorrectLevel.L
          });
          return Boolean(holder.querySelector("canvas") || holder.querySelector("img"));
        } catch (error) {
          console.warn("QR generation skipped:", error?.message || error);
          holder.replaceChildren();
          return false;
        }
      }

      function safeRenderQr(holder, primaryPayload, fallbackPayload, size = 96) {
        if (tryRenderQr(holder, primaryPayload, size)) return true;
        if (tryRenderQr(holder, fallbackPayload, size)) return true;
        holder.replaceChildren();
        const fallback = document.createElement("div");
        fallback.className = "receipt-center receipt-small";
        fallback.textContent = `QR unavailable · ${safeQrText(fallbackPayload, 70)}`;
        holder.appendChild(fallback);
        return false;
      }

      async function createQrCanvas(primaryPayload, fallbackPayload, innerSize = 104, quietZone = 8) {
        if (!window.QRCode) return null;
        const holder = el("qrWorkspace");
        let source = null;
        for (const candidate of [primaryPayload, fallbackPayload]) {
          if (!candidate || !tryRenderQr(holder, candidate, innerSize)) continue;
          await delay(25);
          source = holder.querySelector("canvas");
          if (source) break;
        }
        if (!source) return null;

        const output = document.createElement("canvas");
        output.width = innerSize + quietZone * 2;
        output.height = innerSize + quietZone * 2;
        const context = output.getContext("2d", { willReadFrequently: true });
        context.imageSmoothingEnabled = false;
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, output.width, output.height);
        context.drawImage(source, quietZone, quietZone, innerSize, innerSize);
        return output;
      }

      function canvasToEscPosRaster(canvas) {
        const context = canvas.getContext("2d", { willReadFrequently: true });
        const { width, height } = canvas;
        const pixels = context.getImageData(0, 0, width, height).data;
        const bytesPerRow = Math.ceil(width / 8);
        const raster = new Uint8Array(bytesPerRow * height);

        for (let y = 0; y < height; y += 1) {
          for (let x = 0; x < width; x += 1) {
            const pixelIndex = (y * width + x) * 4;
            const luminance = pixels[pixelIndex] * .299 + pixels[pixelIndex + 1] * .587 + pixels[pixelIndex + 2] * .114;
            if (pixels[pixelIndex + 3] > 20 && luminance < 128) {
              raster[y * bytesPerRow + Math.floor(x / 8)] |= 0x80 >> (x % 8);
            }
          }
        }

        const header = new Uint8Array([
          0x1d, 0x76, 0x30, 0x00,
          bytesPerRow & 0xff, (bytesPerRow >> 8) & 0xff,
          height & 0xff, (height >> 8) & 0xff
        ]);
        const command = new Uint8Array(header.length + raster.length);
        command.set(header, 0);
        command.set(raster, header.length);
        return command;
      }

      function bytesFromAscii(value) {
        const clean = sanitizePrinterText(value);
        const bytes = new Uint8Array(clean.length);
        for (let i = 0; i < clean.length; i += 1) bytes[i] = clean.charCodeAt(i) & 0x7f;
        return bytes;
      }

      function concatBytes(parts) {
        const total = parts.reduce((sum, part) => sum + part.length, 0);
        const result = new Uint8Array(total);
        let offset = 0;
        parts.forEach((part) => { result.set(part, offset); offset += part.length; });
        return result;
      }

      function createAmountCanvas(amount) {
        // Draw at the printer's native 384-dot width for 58 mm paper.
        // Raster text keeps its size and weight even if font commands are ignored.
        const canvas = document.createElement("canvas");
        canvas.width = 384;
        canvas.height = 110;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.fillStyle = "#000";
        context.strokeStyle = "#000";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.font = "900 22px Arial, sans-serif";
        context.fillText("GHS", canvas.width / 2, 16);

        const value = money(amount);
        let fontSize = 72;
        context.font = `900 ${fontSize}px Arial, sans-serif`;
        while (context.measureText(value).width > canvas.width - 16 && fontSize > 12) {
          fontSize -= 1;
          context.font = `900 ${fontSize}px Arial, sans-serif`;
        }
        context.lineWidth = 1.5;
        context.lineJoin = "round";
        context.fillText(value, canvas.width / 2, 68);
        context.strokeText(value, canvas.width / 2, 68);
        return canvas;
      }

      async function buildEscPosReceipt(receipt, format = state.receiptFormat) {
        const ESC = 0x1b;
        const GS = 0x1d;
        const parts = [];
        const command = (...values) => parts.push(new Uint8Array(values));
        const text = (value) => parts.push(bytesFromAscii(value));
        const line = "-".repeat(PAPER_COLUMNS);

        if (format === "short") {
          command(ESC, 0x40);
          command(ESC, 0x61, 0x01);
          command(ESC, 0x45, 0x01);
          command(GS, 0x21, 0x11);
          text("SMART LIVING\n");
          command(GS, 0x21, 0x00);
          text("EMPORIUM PLUS\n");
          command(ESC, 0x45, 0x00);
          text("Head Office: Kaneshie\n0556064611/05099639836/\n0598948132\n");
          wrapText(receipt.branch).forEach((value) => text(`${value}\n`));
          text(`${line}\n`);
          command(ESC, 0x61, 0x00);
          text("CUSTOMER\n");
          command(ESC, 0x45, 0x01);
          wrapText(receipt.customerName).forEach((value) => text(`${value}\n`));
          command(ESC, 0x45, 0x00);
          wrapText(`Phone: ${receipt.customerPhone}`).forEach((value) => text(`${value}\n`));
          text("PRODUCT\n");
          command(ESC, 0x45, 0x01);
          wrapText(receipt.productName).forEach((value) => text(`${value}\n`));
          command(ESC, 0x45, 0x00);
          wrapText(`Agent: ${receipt.agentName}`).forEach((value) => text(`${value}\n`));
          text(`${pairLine("Date:", receipt.dateLabel)}\n`);
          wrapText(`Payment ID: ${receipt.paymentId}`).forEach((value) => text(`${value}\n`));
          command(ESC, 0x61, 0x01);
          command(ESC, 0x45, 0x01);
          text(`${line}\nAMOUNT PAID\n`);
          parts.push(canvasToEscPosRaster(createAmountCanvas(receipt.amount)));
          text(`\n${line}\n`);
          command(ESC, 0x45, 0x00);
          command(ESC, 0x64, 0x02);
          return concatBytes(parts);
        }

        command(ESC, 0x40);                 // Initialize
        command(ESC, 0x61, 0x01);           // Center
        command(GS, 0x21, 0x11);            // Double width/height
        text("SMART LIVING\n");
        command(GS, 0x21, 0x00);
        command(ESC, 0x45, 0x01);           // Bold on
        text("EMPORIUM PLUS\n");
        command(ESC, 0x45, 0x00);           // Bold off
        text("Powering Homes,\nSimplifying Lives.\n");
        text("Head Office: Kaneshie\n");
        wrapText(receipt.branch).forEach((value) => text(`${centered(value)}\n`));
        text(`${line}\n`);
        command(ESC, 0x45, 0x01);
        text("PAYMENT RECEIPT\n");
        command(ESC, 0x45, 0x00);
        command(ESC, 0x61, 0x00);           // Left
        text(`${pairLine("Receipt:", receipt.receiptNumber)}\n`);
        text(`${line}\n`);
        text("CUSTOMER\n");
        command(ESC, 0x45, 0x01);
        wrapText(receipt.customerName).forEach((value) => text(`${value}\n`));
        command(ESC, 0x45, 0x00);
        text(`${pairLine("Customer ID:", receipt.customerId)}\n`);
        text(`${pairLine("Phone:", receipt.customerPhone)}\n`);
        text(`${pairLine("Card No:", receipt.accountNumber || "N/A")}\n`);
        text(`${line}\n`);
        text("PRODUCT\n");
        command(ESC, 0x45, 0x01);
        wrapText(receipt.productName).forEach((value) => text(`${value}\n`));
        command(ESC, 0x45, 0x00);
        text(`${pairLine("Date:", receipt.dateLabel)}\n`);
        text(`${pairLine("Time:", receipt.time)}\n`);
        text(`${pairLine("Method:", receipt.method)}\n`);
        text(`${line}\n`);
        command(ESC, 0x61, 0x01);
        command(ESC, 0x45, 0x01);
        text("AMOUNT PAID\n");
        parts.push(canvasToEscPosRaster(createAmountCanvas(receipt.amount)));
        text("\n");
        command(ESC, 0x45, 0x00);
        command(ESC, 0x61, 0x00);

        if (!receipt.susuMode) {
          text(`${line}\n`);
          command(ESC, 0x61, 0x01);
          command(ESC, 0x45, 0x01);
          text("PAYMENT PROGRESS\n");
          command(ESC, 0x45, 0x00);
          parts.push(canvasToEscPosRaster(createProgressCanvas(receipt)));
          text(`\nProduct total: GHS ${money(receipt.productTotal)}\n`);
          command(ESC, 0x61, 0x00);
        }

        text(`${line}\n`);
        text(`${pairLine("Previously:", `GHS ${money(receipt.previouslyPaid)}`)}\n`);
        text(`${pairLine("Total paid:", `GHS ${money(receipt.totalPaid)}`)}\n`);
        text(`${pairLine("Remaining:", receipt.susuMode ? "N/A" : `GHS ${money(receipt.amountLeft)}`)}\n`);
        text(`${line}\n`);
        text(`${pairLine("Agent:", `${receipt.agentName} (${receipt.agentId || "N/A"})`)}\n`);
        text(`${line}\n`);

        const qrCanvas = await createQrCanvas(receipt.qrPayload, receipt.qrFallback, 104, 8);
        command(ESC, 0x61, 0x01);
        if (qrCanvas) {
          parts.push(canvasToEscPosRaster(qrCanvas));
          text("\nSCAN FOR PAYMENT DETAILS\n");
        } else {
          text(`QR unavailable\n${receipt.receiptNumber}\n${receipt.paymentId}\n`);
        }
        text(`${line}\n`);
        text("Thank you for choosing\nSmart Living.\n");
        command(ESC, 0x45, 0x01);
        text("0556064611/05099639836/\n0598948132\n");
        command(ESC, 0x45, 0x00);
        text("Head Office: Kaneshie\n");
        text("Computer-generated receipt\n");
        text(`${line}\n`);
        command(ESC, 0x64, 0x02);           // Feed 2 lines; printer is manual-tear

        return concatBytes(parts);
      }


const configNode=el('paymentPrinterConfig');
if(!configNode)return;
const config=JSON.parse(configNode.textContent);
const modal=new bootstrap.Modal(el('receiptPrinterModal'));
let port=null,current=null,busy=false,claimed=false,pendingAck=null;
const eligible=(r,format=state.receiptFormat)=>r && !r.legacy_print_locked && ['pending','skipped','failed'].includes(r.receipt_prints?.[format]?.status || (r.printed?'printed':r.print_status));
const pendingAckKey='smartliving.receipt-ack:';
const readableTime=value=>value?new Intl.DateTimeFormat('en-GH',{dateStyle:'medium',timeStyle:'medium',timeZone:'Africa/Accra'}).format(new Date(value.endsWith('Z')?value:value+'Z')):'?';
function message(text){el('receiptConnectionPrompt').textContent=text;}
function supported(){
  if(!window.isSecureContext)throw new Error('Printer connection requires HTTPS or localhost.');
  if(!navigator.serial)throw new Error('Direct printing is unavailable in this browser. Open this page in a browser supporting Web Serial.');
}
function setConnectionState(connected,error=''){
  const button=el('paymentPrinterConnect');
  button.classList.toggle('btn-success',connected);
  button.classList.toggle('btn-danger',!connected);
  button.setAttribute('aria-pressed',String(connected));
  button.title=error||(connected?'Printer connected':'Connect printer');
  el('paymentPrinterConnectLabel').textContent=connected?'Printer connected':'Connect printer';
}
async function connect(chooser=true){
  supported();
  if(port?.writable){setConnectionState(true);return;}
  // Start the chooser directly from the Connect click: browsers require a user gesture.
  if(!port && chooser)port=await navigator.serial.requestPort();
  if(!port){const ports=await navigator.serial.getPorts();port=ports[0];}
  if(!port)throw new Error('Select a printer using Connect & print.');
  if(!port.writable)await port.open({baudRate:9600});
  setConnectionState(true);
}
async function readJson(response){
  if(!(response.headers.get('content-type')||'').includes('application/json')){
    const error=new Error(response.redirected||response.status===401
      ?'Your session has expired. Sign in again and reopen this saved receipt.'
      :`Receipt service returned an unexpected response (${response.status}). Refresh the page; if it continues, contact support.`);
    error.status=response.status;
    throw error;
  }
  return response.json();
}
async function api(id,action,body={}){
  const response=await fetch(config.actionUrl.replace('__PAYMENT_ID__',encodeURIComponent(id)).replace('__ACTION__',encodeURIComponent(action)),{
    method:'POST',headers:{'Content-Type':'application/json','X-Printer-CSRF':config.csrf},body:JSON.stringify(body)
  });
  const data=await readJson(response);
  if(!response.ok || !data.ok){const error=new Error(data.message||'Could not update receipt status.');error.status=response.status;throw error;}
  return data;
}
      function humanDate(isoDate) {
        const parts = String(isoDate || "").split("-").map(Number);
        if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return isoDate || "—";
        return new Intl.DateTimeFormat("en-GH", { day: "2-digit", month: "short", year: "numeric" })
          .format(new Date(parts[0], parts[1] - 1, parts[2]));
      }

      function createQrPayload(receipt) {
        const compactDate = String(receipt.paymentDate || "").replace(/-/g, "");
        return safeQrText(`SL|R=${receipt.receiptNumber}|P=${receipt.paymentId}|C=${receipt.customerId}|A=${Number(receipt.amount).toFixed(2)}|D=${compactDate}`, 180);
      }

      function createQrFallback(receipt) {
        return safeQrText(`SL|R=${receipt.receiptNumber}|P=${receipt.paymentId}`, 90);
      }

      function updateReceiptFormat() {
        const short = state.receiptFormat === "short";
        el("thermalReceipt").classList.toggle("short-receipt", short);
        el("paymentReceiptFormat").value = state.receiptFormat;
        const receipt = state.currentReceipt;
        if (!receipt) return;
        const amount = el("rAmount");
        amount.replaceChildren();
        if (short) {
          const currency = document.createElement("span");
          currency.className = "currency";
          currency.textContent = "GHS";
          amount.append(currency, document.createTextNode(money(receipt.amount)));
          // Keep unusually large amounts on one line on 58 mm paper.
          amount.style.fontSize = `${Math.min(30, 280 / money(receipt.amount).length)}px`;
        } else {
          amount.textContent = `GHS ${money(receipt.amount)}`;
          amount.style.fontSize = "";
        }
      }

function converted(r){
  const paymentDate=r.date.split('/').reverse().join('-');
  const receipt={
    receiptNumber:r.receipt_number,paymentId:r.payment_id,customerName:r.customer,
    customerPhone:r.phone,customerId:r.customer_id,accountNumber:r.account_number,
    productName:r.susu_mode?'SUSU Savings Account':r.description,agentName:r.agent,agentId:r.agent_id,branch:r.branch,
    paymentDate,dateLabel:humanDate(paymentDate),time:r.time,method:r.method,amount:r.amount,susuMode:r.susu_mode,
    productTotal:r.product_total,previouslyPaid:r.susu_mode?0:r.previously_paid,totalPaid:r.susu_mode?r.amount:r.total_paid,
    amountLeft:r.amount_left
  };
  receipt.qrPayload=createQrPayload(receipt);receipt.qrFallback=createQrFallback(receipt);
  return receipt;
}
function preview(){
  if(!current)return;
  const receipt=converted(current);
  state.currentReceipt=receipt;
  const fields={rBranchTop:receipt.branch,rReceiptNo:receipt.receiptNumber,rDate:receipt.dateLabel,
    rTime:receipt.time,rCustomerName:receipt.customerName,rCustomerId:receipt.customerId,
    rCustomerPhone:receipt.customerPhone,rAccount:receipt.accountNumber||'N/A',rProduct:receipt.productName,
    rShortAgent:receipt.agentName,rShortDate:receipt.dateLabel,rShortPaymentId:receipt.paymentId,
    rMethod:receipt.method,rPreviouslyPaid:`GHS ${money(receipt.previouslyPaid)}`,
    rTotalPaid:`GHS ${money(receipt.totalPaid)}`,rAmountLeft:receipt.susuMode?'N/A':`GHS ${money(receipt.amountLeft)}`,
    rAgentLine:`${receipt.agentName} (${receipt.agentId||'N/A'})`};
  for(const [id,value] of Object.entries(fields))el(id).textContent=value||'\u2014';
  updateReceiptFormat();
  el('receiptProgressSection').classList.toggle('d-none',receipt.susuMode);
  const paid=getPaymentProgress(receipt);
  el('rProgressLabel').textContent=`${paid.percent}% PAID`;
  el('rProgressTotal').textContent=`Product total: GHS ${money(paid.total)}`;
  el('rProgressFill').style.width=`${paid.ratio*100}%`;
  el('rProgressBar').setAttribute('aria-valuenow',String(paid.percent));
  safeRenderQr(el('receiptQr'),receipt.qrPayload,receipt.qrFallback,96);
}
function progress(percent,text,written=0,total=0){
  el('receiptPrintAnimation').hidden=false;
  el('receiptPrintState').textContent=text;
  el('receiptPrintPercent').textContent=`${percent}%`;
  el('receiptPrintFill').style.width=`${percent}%`;
  el('receiptPrintProgress').setAttribute('aria-valuenow',percent);
  el('paymentMiniReceipt').style.setProperty('--print-progress',percent/100);

}
function controls(){
  el('receiptConnectAndPrint').hidden=!eligible(current)||claimed;
  el('receiptPrintDone').hidden=eligible(current)&&!claimed&&!current?.printed;
  el('receiptConnectAndPrint').disabled=busy;
  el('receiptPrintDone').disabled=busy;
  el('paymentReceiptFormat').disabled=busy||Boolean(pendingAck);
}
async function finish(id,token,outcome,format='short'){
  // Retry the acknowledgement only. Never resend receipt bytes.
  let last;
  for(let attempt=0;attempt<3;attempt++){
    try{return await api(id,'finish',{token,outcome,format});}
    catch(error){last=error;if(attempt<2)await delay(500);}
  }
  throw last;
}
async function print(choose=false){
  if(busy||claimed||!eligible(current))return;
  el('receiptPrintAnimation').classList.remove('error');
  busy=true;controls();let token=null,writer=null,allSent=false;
  const receipt=current;
  const receiptFormat=state.receiptFormat;
  try{
    message('Connecting to printer…');
    await connect(choose);
    progress(0,'Preparing receipt…');
    const bytes=await buildEscPosReceipt(converted(receipt),state.receiptFormat);
    // Acquire the writer before reserving the receipt. No bytes have been sent yet.
    writer=port.writable.getWriter();
    const claim=await api(receipt.document_id,'claim',{format:receiptFormat});
    token=claim.token;claimed=true;controls();
    message('Printing your receipt. Please keep this page open.');
    for(let offset=0;offset<bytes.length;offset+=256){
      const chunk=bytes.slice(offset,offset+256);
      await writer.write(chunk);
      const written=Math.min(offset+chunk.length,bytes.length);
      progress(Math.floor(written/bytes.length*100),'Printing receipt…',written,bytes.length);
      if(written<bytes.length)await delay(12);
    }
    allSent=true;
    const ack={id:receipt.document_id,token,format:receiptFormat};
    pendingAck=ack;
    try{localStorage.setItem(pendingAckKey+ack.id+':'+ack.format,JSON.stringify(ack));}catch(_){}
    const result=await finish(receipt.document_id,token,'printed',receiptFormat);
    current=result.receipt;
    pendingAck=null;
    try{localStorage.removeItem(pendingAckKey+receipt.document_id+':'+receiptFormat);}catch(_){}
    message(`Receipt sent successfully. Recorded at ${readableTime(current.recorded_at)} · Printed at ${readableTime(current.printed_at)} · Delay ${Number(current.print_delay_seconds).toFixed(1)} seconds.`);
    progress(100,'Receipt printed successfully',bytes.length,bytes.length);
  }catch(error){
    if(token){
      current.print_status='uncertain';
      current.receipt_prints[receiptFormat].status='uncertain';
      if(!allSent){try{await finish(receipt.document_id,token,'uncertain',receiptFormat);}catch(_){} }
      message(allSent?'Receipt data was sent, but saving the print result failed. Reopen this page to retry saving the result. The receipt will not be sent again.':'Printing was interrupted. The payment is saved. This receipt is locked to prevent duplicate printing.');
      progress(0,allSent?'Print confirmation pending':'Print result uncertain');
    }else{
      if(error.status===409||error.status===403){claimed=true;current.receipt_prints[receiptFormat].status='unavailable';}
      message(error.name==='NotFoundError'?'Printer selection cancelled. Connect your printer to continue.':error.message);
      progress(0,'Receipt not sent');
    }
    el('receiptPrintAnimation').classList.add('error');
  }finally{
    if(writer)writer.releaseLock();
    busy=false;controls();
    if(pendingAck)el('receiptPrintDone').textContent='Retry saving print result';
    loadPending();
  }
}
function open(receipt){
  if(busy||pendingAck)return;
  if(!current||current.document_id!==receipt.document_id)state.receiptFormat='short';
  current=receipt;claimed=!eligible(receipt);
  el('receiptPrintAnimation').hidden=true;
  el('receiptPrintAnimation').classList.remove('error');
  el('receiptPrintDone').textContent='Done';
  preview();controls();
  message(eligible(receipt)?'Payment saved. Connect your printer to print this receipt automatically.':'This receipt format has already been printed or is unavailable. You can select the other format.');
  el('receiptConnectAndPrint').textContent=port?.writable?'Print receipt':'Connect & print';
  modal.show();
  if(eligible(receipt)&&port?.writable)print(false);
}
async function loadPending(){
  const box=el('pendingPrinterReceipts');
  try{
    const response=await fetch(config.pendingUrl);
    const data=await readJson(response);
    if(!response.ok)throw new Error(data.message||'Could not load receipts.');
    const count=Number(data.count??data.receipts.length);
    const badge=el('pendingPrinterCount');
    badge.textContent=String(count);
    badge.setAttribute('aria-label',`${count} unprinted receipts today`);
    badge.classList.toggle('text-bg-danger',count>0);
    badge.classList.toggle('text-bg-secondary',count===0);
    box.replaceChildren();
    for(const receipt of data.receipts){
      const row=document.createElement('div');row.className='d-flex justify-content-between align-items-center gap-2 border-top py-2';
      const label=document.createElement('span');label.textContent=`${receipt.customer} · GHS ${receipt.amount} · ${receipt.print_status}`;
      const button=document.createElement('button');button.type='button';button.className='btn btn-sm btn-outline-primary';button.textContent=eligible(receipt)?'Open receipt':'View status';
      button.addEventListener('click',()=>open(receipt));row.append(label,button);box.append(row);
    }
    if(!data.receipts.length)box.textContent='No unprinted receipts.';
  }catch(error){
    const badge=el('pendingPrinterCount');
    badge.textContent='?';
    badge.setAttribute('aria-label','Unprinted receipt count unavailable');
    box.textContent=error.message;
  }
}
el('receiptConnectAndPrint').addEventListener('click',()=>print(!port?.writable));
el('receiptPrintDone').addEventListener('click',async()=>{
  if(busy)return;
  if(!pendingAck){modal.hide();return;}
  busy=true;controls();
  try{
    const result=await finish(pendingAck.id,pendingAck.token,'printed',pendingAck.format||'short');
    try{localStorage.removeItem(pendingAckKey+pendingAck.id+':'+(pendingAck.format||'short'));}catch(_){}
    pendingAck=null;current=result.receipt;
    message('Print result saved.');
    progress(100,'Receipt printed successfully');
    el('receiptPrintDone').textContent='Done';
    await loadPending();
  }catch(error){message('Could not save print confirmation. Check your connection and try again.');}
  finally{busy=false;controls();}
});
el('paymentReceiptFormat').addEventListener('change',event=>{
  state.receiptFormat=event.target.value;
  claimed=!eligible(current);
  preview();controls();
  el('receiptPrintAnimation').hidden=true;
  message(eligible(current)?'Ready to print this receipt format.':'This receipt format has already been printed or is unavailable.');
});
el('paymentPrinterConnect').addEventListener('click',async()=>{
  try{await connect(true);}catch(error){setConnectionState(false,error.name==='NotFoundError'?'Printer selection cancelled.':error.message);}
});
if(navigator.serial)navigator.serial.addEventListener('disconnect',()=>{
  port=null;setConnectionState(false,'Printer disconnected. Reconnect to print.');
});
window.PaymentPrinter={open};
window.addEventListener('beforeunload',event=>{if(busy){event.preventDefault();event.returnValue='';}});
async function init(){
  // Recover an acknowledgement after a reload, without printing again.
  try{
    const keys=Object.keys(localStorage).filter(key=>key.startsWith(pendingAckKey));
    for(const key of keys){const ack=JSON.parse(localStorage.getItem(key));await finish(ack.id,ack.token,'printed',ack.format||'short');localStorage.removeItem(key);}
  }catch(_){setConnectionState(false,'A print confirmation is pending. Refresh to retry.');}
  try{supported();const ports=await navigator.serial.getPorts();if(ports.length){port=ports[0];await connect(false);}}
  catch(error){setConnectionState(false,error.message);}
  await loadPending();
}
init();
})();

