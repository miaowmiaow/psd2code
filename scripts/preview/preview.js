(() => {
  const frame = document.getElementById('preview-frame');
  const overlay = document.getElementById('overlay');
  const layerList = document.getElementById('layer-list');
  const info = document.getElementById('info');
  const toggleOutline = document.getElementById('toggle-outline');
  const scaleInput = document.getElementById('scale');
  const infoListPanel = document.getElementById('info-list');
  const infoDetails = document.getElementById('info-details');
  let overlaySvg = null;
  let currentConnector = null;

  function qsaSafe(root, sel){
    try{return Array.from(root.querySelectorAll(sel))}catch(e){return []}
  }

  function findLayerNodes(doc){
    // 常见的 PSD -> HTML 元数据属性
    const selectors = ['[data-layer-name]','[data-name]','[data-layer]','[data-psd-id]'];
      for(const s of selectors){
        const nodes = qsaSafe(doc, s);
        if(nodes.length) return nodes;
      }
    // 回退：选择 body 下的可视元素（排除脚本与样式）
    const all = Array.from(doc.body.querySelectorAll('*')).filter(el=>{
      const r = el.clientWidth>0 && el.clientHeight>0 && getComputedStyle(el).display!=='none';
      return r && !['SCRIPT','STYLE','META','LINK'].includes(el.tagName);
    });
    return all.slice(0,500); // 限制避免性能问题
  }

  function getNameFor(el){
    return el.getAttribute('data-layer-name')||el.getAttribute('data-name')||el.getAttribute('id')||el.className||el.tagName.toLowerCase();
  }

  function clearOverlay(){
    overlay.innerHTML = '';
    // create an SVG layer for connector lines
    try{
      overlaySvg = document.createElementNS('http://www.w3.org/2000/svg','svg');
      overlay.appendChild(overlaySvg);
    }catch(e){ overlaySvg = null; }
  }
  let activeGapEls = [];

  function clearGaps(){
    activeGapEls.forEach(el=>el.remove());
    activeGapEls.length = 0;
  }

  function clearConnector(){
    try{ if(overlaySvg) overlaySvg.innerHTML = ''; }catch(e){}
    currentConnector = null;
  }

  function makeBoxFor(rect,name,scale,containerRect){
    const ox = containerRect ? containerRect.left : 0;
    const oy = containerRect ? containerRect.top : 0;
    const box = document.createElement('div');
    box.className='layer-box';
    box.style.left = (rect.left - ox)*scale + 'px';
    box.style.top = (rect.top - oy)*scale + 'px';
    box.style.width = rect.width*scale + 'px';
    box.style.height = rect.height*scale + 'px';
    // 仅创建遮罩框（移除尺寸徽章与文字标签，减少干扰）
    overlay.appendChild(box);
    return {box};
  }

  // 全局选中状态
  let selectedMeta = null; // {el, rect, containerRect, scale, box, label, name}
  // overlay 内显示选中信息的元素（即使侧栏隐藏也可见）
  let selectedInfoEl = null;

  function clearSelected(){
    if(!selectedMeta) return;
    try{ selectedMeta.box.classList.remove('selected'); }catch(e){}
    selectedMeta = null;
    try{ if(selectedInfoEl){ selectedInfoEl.style.display = 'none'; } }catch(e){}
  }

  function setSelected(meta){
    // meta: {el, rect, containerRect, scale, box, label, name}
    clearSelected();
    selectedMeta = meta;
    try{ selectedMeta.box.classList.add('selected');
      // ensure selected visuals visible
      selectedMeta.box.classList.add('visible');
    }catch(e){}
    // update info pane (sidebar hidden by default)
    const infoText = `${meta.name} — ${Math.round(meta.rect.left)},${Math.round(meta.rect.top)} · ${Math.round(meta.rect.width)}×${Math.round(meta.rect.height)}`;
    info.textContent = infoText;
    // do not show inline selected-info popup (right panel will display details)
    // show details in right panel (no list)
    try{
      if(infoDetails){
        // gather extra computed style info if available
        const el = selectedMeta.el;
        const cs = el && el.ownerDocument ? el.ownerDocument.defaultView.getComputedStyle(el) : null;
        const opacity = cs ? cs.opacity : '';
        const bg = cs ? (cs.backgroundColor||'') : '';
        const font = cs ? (cs.fontFamily||'') : '';
        infoDetails.innerHTML = `<div><strong>${selectedMeta.name}</strong></div><div>位置: ${Math.round(selectedMeta.rect.left)}, ${Math.round(selectedMeta.rect.top)}</div><div>大小: ${Math.round(selectedMeta.rect.width)}×${Math.round(selectedMeta.rect.height)}</div><div>不透明度: ${opacity}</div><div>背景: ${bg}</div><div>字体: ${font}</div>`;
      }
    }catch(e){}
  }

  function showGaps(rectA, rectB, containerRect, scale){
    clearGaps();
    if(!rectA || !rectB) return;
    // 计算水平间距并在两者之间放置标签
    const A = rectA; const B = rectB;
    const leftA = (A.left - containerRect.left)*scale;
    const rightA = leftA + A.width*scale;
    const leftB = (B.left - containerRect.left)*scale;
    const rightB = leftB + B.width*scale;
    const topA = (A.top - containerRect.top)*scale;
    const bottomA = topA + A.height*scale;
    const topB = (B.top - containerRect.top)*scale;
    const bottomB = topB + B.height*scale;

    // also draw side connectors (left/top/right/bottom) if svg available
    try{ drawSideConnectors(rectA, rectB, containerRect, scale); }catch(e){}
  }

  function drawConnector(A, B, containerRect, scale){
    // legacy center connector (not used)
    clearConnector();
    if(!overlaySvg) return;
    const ax = (A.left - containerRect.left + A.width/2)*scale;
    const ay = (A.top - containerRect.top + A.height/2)*scale;
    const bx = (B.left - containerRect.left + B.width/2)*scale;
    const by = (B.top - containerRect.top + B.height/2)*scale;
    const line = document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1', ax);
    line.setAttribute('y1', ay);
    line.setAttribute('x2', bx);
    line.setAttribute('y2', by);
    line.setAttribute('class','connector');
    overlaySvg.appendChild(line);
    currentConnector = line;
  }

  function drawSideConnectors(A, B, containerRect, scale){
    // A = selected, B = hover
    clearConnector();
    if(!overlaySvg) return;

    const ax0 = (A.left - containerRect.left)*scale;
    const ay0 = (A.top - containerRect.top)*scale;
    const ax1 = (A.left + A.width - containerRect.left)*scale;
    const ay1 = (A.top + A.height - containerRect.top)*scale;
    const bx0 = (B.left - containerRect.left)*scale;
    const by0 = (B.top - containerRect.top)*scale;
    const bx1 = (B.left + B.width - containerRect.left)*scale;
    const by1 = (B.top + B.height - containerRect.top)*scale;

    const A_cx = (ax0 + ax1)/2, A_cy = (ay0 + ay1)/2;
    const B_cx = (bx0 + bx1)/2, B_cy = (by0 + by1)/2;

    const addPath = (pts, cls)=>{
      const p = document.createElementNS('http://www.w3.org/2000/svg','path');
      const d = pts.map((pt,i)=> (i===0? 'M':'L') + ' ' + Math.round(pt[0]) + ' ' + Math.round(pt[1])).join(' ');
      p.setAttribute('d', d);
      p.setAttribute('fill','none');
      p.setAttribute('class', cls);
      overlaySvg.appendChild(p);
      return p;
    };

    const bubbleRects = [];
    const rectIntersects = (a, b, pad = 4)=> !(a.right + pad < b.left || a.left - pad > b.right || a.bottom + pad < b.top || a.top - pad > b.bottom);

    const addBubble = (x, y, text, cls)=>{
      const bubble = document.createElement('div');
      bubble.className = 'gap-label ' + cls;
      bubble.textContent = Math.round(text) + 'px';
      bubble.style.visibility = 'hidden';
      bubble.style.left = Math.round(x) + 'px';
      bubble.style.top = Math.round(y) + 'px';
      overlay.appendChild(bubble);

      // P2: 气泡自动避让（优先沿与主轴线垂直方向移动）
      const w = bubble.offsetWidth || 46;
      const h = bubble.offsetHeight || 22;
      const isHorizontalBubble = cls.indexOf('gap-h') >= 0;
      const step = isHorizontalBubble ? (h + 8) : (w + 8);
      const candidates = [0, step, -step, step * 2, -step * 2, step * 3, -step * 3];

      let chosenX = x;
      let chosenY = y;
      for(const delta of candidates){
        const cx = isHorizontalBubble ? x : (x + delta);
        const cy = isHorizontalBubble ? (y + delta) : y;
        const rect = {
          left: cx - w / 2,
          right: cx + w / 2,
          top: cy - h / 2,
          bottom: cy + h / 2,
        };
        const collides = bubbleRects.some(r => rectIntersects(rect, r, 4));
        if(!collides){
          chosenX = cx;
          chosenY = cy;
          bubbleRects.push(rect);
          break;
        }
      }

      bubble.style.left = Math.round(chosenX) + 'px';
      bubble.style.top = Math.round(chosenY) + 'px';
      bubble.style.visibility = 'visible';
      activeGapEls.push(bubble);
    };

    // 分层参数
    const H_BUBBLE_OFFSET = 26;
    const V_BUBBLE_OFFSET = 34;
    const TICK = 6;

    // 统一锚点：A 为已选中元素；包含判定加容差，避免阴影/小数像素导致误判
    const CONTAIN_EPS = 1;
    const aContainsBX = (ax0 - CONTAIN_EPS) <= bx0 && (ax1 + CONTAIN_EPS) >= bx1;
    const aContainsBY = (ay0 - CONTAIN_EPS) <= by0 && (ay1 + CONTAIN_EPS) >= by1;
    const bContainsAX = (bx0 - CONTAIN_EPS) <= ax0 && (bx1 + CONTAIN_EPS) >= ax1;
    const bContainsAY = (by0 - CONTAIN_EPS) <= ay0 && (by1 + CONTAIN_EPS) >= ay1;
    const xContains = aContainsBX || bContainsAX;
    const yContains = aContainsBY || bContainsAY;

    // 几何重叠：用于约束“包裹”必须发生在真实重叠场景，避免跨区域误判
    const overlapW = Math.min(ax1, bx1) - Math.max(ax0, bx0);
    const overlapH = Math.min(ay1, by1) - Math.max(ay0, by0);
    const overlapArea = overlapW > CONTAIN_EPS && overlapH > CONTAIN_EPS;
    const overlapCenterX = overlapW > CONTAIN_EPS ? (Math.max(ax0, bx0) + Math.min(ax1, bx1)) / 2 : null;
    const pairMidX = (A_cx + B_cx) / 2;
    const pairMidY = (A_cy + B_cy) / 2;

    const aContainsB = aContainsBX && aContainsBY && overlapArea;
    const bContainsA = bContainsAX && bContainsAY && overlapArea;

    const addTick = (x, y, axis)=>{
      if(axis === 'h') addPath([[x, y - TICK], [x, y + TICK]], 'connector-dashed');
      else addPath([[x - TICK, y], [x + TICK, y]], 'connector-dashed');
    };

    const horizontalBubbleY = (yGuide)=> yGuide + ((yGuide <= pairMidY) ? -H_BUBBLE_OFFSET : H_BUBBLE_OFFSET);
    const verticalBubbleX = (xGuide)=> xGuide + ((xGuide >= pairMidX) ? V_BUBBLE_OFFSET : -V_BUBBLE_OFFSET);

    const drawHorizontalMeasure = (xStart, xEnd, yGuide, hoverAnchorX, hoverAnchorY)=>{
      const len = Math.abs(xEnd - xStart);
      if(len <= 0) return false;
      addPath([[xStart, yGuide], [xEnd, yGuide]], 'connector');
      addTick(xStart, yGuide, 'h');
      addTick(xEnd, yGuide, 'h');
      // 蓝投影：hover 锚点正交投到最近的测量端点（顺序无关）
      if(Math.abs(hoverAnchorY - yGuide) > 0.5){
        const targetX = Math.abs(hoverAnchorX - xStart) <= Math.abs(hoverAnchorX - xEnd) ? xStart : xEnd;
        addPath([[hoverAnchorX, hoverAnchorY], [targetX, yGuide]], 'connector-dashed');
      }
      addBubble((xStart + xEnd)/2, horizontalBubbleY(yGuide), len, 'gap-h');
      return true;
    };

    const drawVerticalMeasure = (yStart, yEnd, xGuide, hoverAnchorX, hoverAnchorY)=>{
      const len = Math.abs(yEnd - yStart);
      if(len <= 0) return false;
      addPath([[xGuide, yStart], [xGuide, yEnd]], 'connector');
      addTick(xGuide, yStart, 'v');
      addTick(xGuide, yEnd, 'v');
      // 蓝投影：hover 锚点正交投到最近的测量端点（顺序无关）
      if(Math.abs(hoverAnchorX - xGuide) > 0.5){
        const targetY = Math.abs(hoverAnchorY - yStart) <= Math.abs(hoverAnchorY - yEnd) ? yStart : yEnd;
        addPath([[hoverAnchorX, hoverAnchorY], [xGuide, targetY]], 'connector-dashed');
      }
      addBubble(verticalBubbleX(xGuide), (yStart + yEnd)/2, len, 'gap-v');
      return true;
    };

    // 双轴包裹：关系判断无方向，A/B 对调时保持一致
    if(aContainsB || bContainsA){
      const outer = aContainsB
        ? {x0: ax0, y0: ay0, x1: ax1, y1: ay1}
        : {x0: bx0, y0: by0, x1: bx1, y1: by1};
      const inner = aContainsB
        ? {x0: bx0, y0: by0, x1: bx1, y1: by1}
        : {x0: ax0, y0: ay0, x1: ax1, y1: ay1};
      const innerCx = (inner.x0 + inner.x1) / 2;
      const innerCy = (inner.y0 + inner.y1) / 2;

      const leftGap = Math.abs(inner.x0 - outer.x0);
      const rightGap = Math.abs(outer.x1 - inner.x1);
      const topGap = Math.abs(inner.y0 - outer.y0);
      const bottomGap = Math.abs(outer.y1 - inner.y1);

      if(leftGap > 0){
        addPath([[outer.x0, innerCy], [inner.x0, innerCy]], 'connector');
        addBubble((outer.x0 + inner.x0)/2, horizontalBubbleY(innerCy), leftGap, 'gap-h');
      }
      if(rightGap > 0){
        addPath([[inner.x1, innerCy], [outer.x1, innerCy]], 'connector');
        addBubble((inner.x1 + outer.x1)/2, horizontalBubbleY(innerCy), rightGap, 'gap-h');
      }
      if(topGap > 0){
        addPath([[innerCx, outer.y0], [innerCx, inner.y0]], 'connector');
        addBubble(verticalBubbleX(innerCx), (outer.y0 + inner.y0)/2, topGap, 'gap-v');
      }
      if(bottomGap > 0){
        addPath([[innerCx, inner.y1], [innerCx, outer.y1]], 'connector');
        addBubble(verticalBubbleX(innerCx), (inner.y1 + outer.y1)/2, bottomGap, 'gap-v');
      }
      return;
    }

    // 轴向间隔：仅在区间真正分离时才有该轴间距
    const xGap = bx0 > ax1 ? (bx0 - ax1) : (ax0 > bx1 ? (ax0 - bx1) : 0);
    const yGap = by0 > ay1 ? (by0 - ay1) : (ay0 > by1 ? (ay0 - by1) : 0);
    let xMode = 'none';
    let yMode = 'none';
    let drewX = false;
    let drewY = false;

    // X 轴主测量：关系判断无方向
    const xContained = (aContainsBX || bContainsAX) && overlapH > CONTAIN_EPS;
    if(xContained){
      const outerX = aContainsBX
        ? {x0: ax0, x1: ax1, y0: ay0, y1: ay1}
        : {x0: bx0, x1: bx1, y0: by0, y1: by1};
      const innerX = aContainsBX
        ? {x0: bx0, x1: bx1, cy: B_cy}
        : {x0: ax0, x1: ax1, cy: A_cy};
      const outerCy = (outerX.y0 + outerX.y1) / 2;
      const guideY = innerX.cy;
      const outerNearY = (guideY >= outerCy) ? outerX.y1 : outerX.y0;

      // 包裹场景下只保留粉主线，蓝线由外层边缘投影补齐
      const drewLeft = drawHorizontalMeasure(outerX.x0, innerX.x0, guideY, innerX.x0, guideY);
      const drewRight = drawHorizontalMeasure(outerX.x1, innerX.x1, guideY, innerX.x1, guideY);
      if(drewLeft || drewRight){
        xMode = 'contain';
        drewX = true;
      }

      // 蓝辅助线：外层左右边投影到水平测量导轨
      if((drewLeft || drewRight) && Math.abs(outerNearY - guideY) > 0.5){
        addPath([[outerX.x0, outerNearY], [outerX.x0, guideY]], 'connector-dashed');
        addPath([[outerX.x1, outerNearY], [outerX.x1, guideY]], 'connector-dashed');
      }
    }else if(xGap > CONTAIN_EPS){
      // 最近边（仅在 x 轴分离时才绘制）
      const drew = (B_cx >= A_cx)
        ? drawHorizontalMeasure(ax1, bx0, A_cy, bx0, B_cy)
        : drawHorizontalMeasure(ax0, bx1, A_cy, bx1, B_cy);
      if(drew){
        xMode = 'separated';
        drewX = true;
      }
    }else if(yGap > CONTAIN_EPS && overlapW > CONTAIN_EPS){
      // A/B 在 x 轴有重叠且 y 轴分离：补充外边缘 x 连线（左右边缘差值）
      // 左右两条线分别挂在“该侧起点归属图层”的 y 中心
      let branchDrewX = false;
      const leftGap = Math.abs(ax0 - bx0);
      const rightGap = Math.abs(bx1 - ax1);
      // 左侧边距源：left 边更靠右的一侧（max(left)）
      const leftSourceIsA = ax0 >= bx0;
      const rightSourceIsA = ax1 <= bx1;
      const leftGuideY = leftSourceIsA ? A_cy : B_cy;
      const rightGuideY = rightSourceIsA ? A_cy : B_cy;

      if(leftGap > CONTAIN_EPS){
        const leftStartX = leftSourceIsA ? ax0 : bx0;
        const leftEndX = leftSourceIsA ? bx0 : ax0;
        addPath([[leftStartX, leftGuideY], [leftEndX, leftGuideY]], 'connector');
        branchDrewX = true;
        addTick(leftStartX, leftGuideY, 'h');
        addTick(leftEndX, leftGuideY, 'h');
        addBubble((leftStartX + leftEndX) / 2, horizontalBubbleY(leftGuideY), leftGap, 'gap-h');
      }
      if(rightGap > CONTAIN_EPS){
        const rightStartX = rightSourceIsA ? ax1 : bx1;
        const rightEndX = rightSourceIsA ? bx1 : ax1;
        addPath([[rightStartX, rightGuideY], [rightEndX, rightGuideY]], 'connector');
        branchDrewX = true;
        addTick(rightStartX, rightGuideY, 'h');
        addTick(rightEndX, rightGuideY, 'h');
        addBubble((rightStartX + rightEndX) / 2, horizontalBubbleY(rightGuideY), rightGap, 'gap-h');
      }

      if(branchDrewX){
        xMode = 'outer-edge';
        drewX = true;
      }

      // 蓝辅助线：按左右各自导轨投影
      const aNearYForLeft = (leftGuideY >= A_cy) ? ay1 : ay0;
      const bNearYForLeft = (leftGuideY >= B_cy) ? by1 : by0;
      if(leftGap > CONTAIN_EPS){
        if(Math.abs(aNearYForLeft - leftGuideY) > 0.5) addPath([[ax0, aNearYForLeft], [ax0, leftGuideY]], 'connector-dashed');
        if(Math.abs(bNearYForLeft - leftGuideY) > 0.5) addPath([[bx0, bNearYForLeft], [bx0, leftGuideY]], 'connector-dashed');
      }

      const aNearYForRight = (rightGuideY >= A_cy) ? ay1 : ay0;
      const bNearYForRight = (rightGuideY >= B_cy) ? by1 : by0;
      if(rightGap > CONTAIN_EPS){
        if(Math.abs(aNearYForRight - rightGuideY) > 0.5) addPath([[ax1, aNearYForRight], [ax1, rightGuideY]], 'connector-dashed');
        if(Math.abs(bNearYForRight - rightGuideY) > 0.5) addPath([[bx1, bNearYForRight], [bx1, rightGuideY]], 'connector-dashed');
      }
    }

    // Y 轴主测量：关系判断无方向
    const yContained = (aContainsBY || bContainsAY);
    if(yContained){
      const outerY = aContainsBY
        ? {y0: ay0, y1: ay1, x0: ax0, x1: ax1}
        : {y0: by0, y1: by1, x0: bx0, x1: bx1};
      const innerY = aContainsBY
        ? {y0: by0, y1: by1, cx: B_cx}
        : {y0: ay0, y1: ay1, cx: A_cx};
      const outerCx = (outerY.x0 + outerY.x1) / 2;
      const guideX = overlapCenterX !== null ? overlapCenterX : innerY.cx;
      const outerNearX = (guideX >= outerCx) ? outerY.x1 : outerY.x0;

      // 包裹场景下只保留粉主线，蓝线由外层边缘投影补齐
      const drewTop = drawVerticalMeasure(outerY.y0, innerY.y0, guideX, guideX, innerY.y0);
      const drewBottom = drawVerticalMeasure(outerY.y1, innerY.y1, guideX, guideX, innerY.y1);
      if(drewTop || drewBottom){
        yMode = 'contain';
        drewY = true;
      }

      // 蓝辅助线：外层上下边投影到垂直测量导轨
      if((drewTop || drewBottom) && Math.abs(outerNearX - guideX) > 0.5){
        addPath([[outerNearX, outerY.y0], [guideX, outerY.y0]], 'connector-dashed');
        addPath([[outerNearX, outerY.y1], [guideX, outerY.y1]], 'connector-dashed');
      }
    }else if(yGap > CONTAIN_EPS){
      // 最近边（仅在 y 轴分离时才绘制）
      const guideX = overlapCenterX !== null ? overlapCenterX : A_cx;
      const drew = (B_cy >= A_cy)
        ? drawVerticalMeasure(ay1, by0, guideX, B_cx, by0)
        : drawVerticalMeasure(ay0, by1, guideX, B_cx, by1);
      if(drew){
        yMode = 'separated';
        drewY = true;
      }
    }else if(xGap > CONTAIN_EPS && overlapH > CONTAIN_EPS){
      // A/B 在 y 轴有重叠且 x 轴分离：补充外边缘 y 连线（上下边缘差值）
      // 上下两条线分别挂在“该侧起点归属图层”的 x 中心
      let branchDrewY = false;
      const topGap = Math.abs(ay0 - by0);
      const bottomGap = Math.abs(by1 - ay1);
      // 上侧边距源：top 边更靠下的一侧（max(top)）
      const topSourceIsA = ay0 >= by0;
      // 下侧边距源：bottom 边更靠上的一侧（min(bottom)）
      const bottomSourceIsA = ay1 <= by1;
      const topGuideX = topSourceIsA ? A_cx : B_cx;
      const bottomGuideX = bottomSourceIsA ? A_cx : B_cx;

      if(topGap > CONTAIN_EPS){
        const topStartY = topSourceIsA ? ay0 : by0;
        const topEndY = topSourceIsA ? by0 : ay0;
        addPath([[topGuideX, topStartY], [topGuideX, topEndY]], 'connector');
        branchDrewY = true;
        addTick(topGuideX, topStartY, 'v');
        addTick(topGuideX, topEndY, 'v');
        addBubble(verticalBubbleX(topGuideX), (topStartY + topEndY) / 2, topGap, 'gap-v');
      }

      if(bottomGap > CONTAIN_EPS){
        const bottomStartY = bottomSourceIsA ? ay1 : by1;
        const bottomEndY = bottomSourceIsA ? by1 : ay1;
        addPath([[bottomGuideX, bottomStartY], [bottomGuideX, bottomEndY]], 'connector');
        branchDrewY = true;
        addTick(bottomGuideX, bottomStartY, 'v');
        addTick(bottomGuideX, bottomEndY, 'v');
        addBubble(verticalBubbleX(bottomGuideX), (bottomStartY + bottomEndY) / 2, bottomGap, 'gap-v');
      }

      if(branchDrewY){
        yMode = 'outer-edge';
        drewY = true;
      }

      // 蓝辅助线：按上下各自导轨投影
      const aNearXForTop = (topGuideX >= A_cx) ? ax1 : ax0;
      const bNearXForTop = (topGuideX >= B_cx) ? bx1 : bx0;
      if(topGap > CONTAIN_EPS){
        if(Math.abs(aNearXForTop - topGuideX) > 0.5) addPath([[aNearXForTop, ay0], [topGuideX, ay0]], 'connector-dashed');
        if(Math.abs(bNearXForTop - topGuideX) > 0.5) addPath([[bNearXForTop, by0], [topGuideX, by0]], 'connector-dashed');
      }

      const aNearXForBottom = (bottomGuideX >= A_cx) ? ax1 : ax0;
      const bNearXForBottom = (bottomGuideX >= B_cx) ? bx1 : bx0;
      if(bottomGap > CONTAIN_EPS){
        if(Math.abs(aNearXForBottom - bottomGuideX) > 0.5) addPath([[aNearXForBottom, ay1], [bottomGuideX, ay1]], 'connector-dashed');
        if(Math.abs(bNearXForBottom - bottomGuideX) > 0.5) addPath([[bNearXForBottom, by1], [bottomGuideX, by1]], 'connector-dashed');
      }
    }

    // edge-delta 兜底：按轴独立补画，避免“Y 有标注但 X 无标注”
    const leftGap = Math.abs(ax0 - bx0);
    const rightGap = Math.abs(ax1 - bx1);
    const topGap = Math.abs(ay0 - by0);
    const bottomGap = Math.abs(ay1 - by1);

    // X 轴兜底：仅当 X 轴尚未命中且左右边缘差值存在时强制补画
    // 先确保“应显示的 X 标注”不会再被其他门槛误伤。
    if(!drewX && (leftGap > CONTAIN_EPS || rightGap > CONTAIN_EPS)){
      xMode = 'edge-delta';
      const leftSourceIsA = ax0 >= bx0;
      const rightSourceIsA = ax1 <= bx1;
      const leftGuideY = leftSourceIsA ? A_cy : B_cy;
      const rightGuideY = rightSourceIsA ? A_cy : B_cy;

      if(leftGap > CONTAIN_EPS){
        const leftStartX = leftSourceIsA ? ax0 : bx0;
        const leftEndX = leftSourceIsA ? bx0 : ax0;
        addPath([[leftStartX, leftGuideY], [leftEndX, leftGuideY]], 'connector');
        drewX = true;
        addTick(leftStartX, leftGuideY, 'h');
        addTick(leftEndX, leftGuideY, 'h');
        addBubble((leftStartX + leftEndX) / 2, horizontalBubbleY(leftGuideY), leftGap, 'gap-h');
      }

      if(rightGap > CONTAIN_EPS){
        const rightStartX = rightSourceIsA ? ax1 : bx1;
        const rightEndX = rightSourceIsA ? bx1 : ax1;
        addPath([[rightStartX, rightGuideY], [rightEndX, rightGuideY]], 'connector');
        drewX = true;
        addTick(rightStartX, rightGuideY, 'h');
        addTick(rightEndX, rightGuideY, 'h');
        addBubble((rightStartX + rightEndX) / 2, horizontalBubbleY(rightGuideY), rightGap, 'gap-h');
      }
    }

    // Y 轴兜底：仅当 Y 轴尚未命中，且 x 轴有重叠
    if(!drewY && overlapW > CONTAIN_EPS && (topGap > CONTAIN_EPS || bottomGap > CONTAIN_EPS)){
      yMode = 'edge-delta';
      const topSourceIsA = ay0 >= by0;
      const bottomSourceIsA = ay1 <= by1;
      const topGuideX = topSourceIsA ? A_cx : B_cx;
      const bottomGuideX = bottomSourceIsA ? A_cx : B_cx;

      if(topGap > CONTAIN_EPS){
        const topStartY = topSourceIsA ? ay0 : by0;
        const topEndY = topSourceIsA ? by0 : ay0;
        addPath([[topGuideX, topStartY], [topGuideX, topEndY]], 'connector');
        drewY = true;
        addTick(topGuideX, topStartY, 'v');
        addTick(topGuideX, topEndY, 'v');
        addBubble(verticalBubbleX(topGuideX), (topStartY + topEndY) / 2, topGap, 'gap-v');
      }

      if(bottomGap > CONTAIN_EPS){
        const bottomStartY = bottomSourceIsA ? ay1 : by1;
        const bottomEndY = bottomSourceIsA ? by1 : ay1;
        addPath([[bottomGuideX, bottomStartY], [bottomGuideX, bottomEndY]], 'connector');
        drewY = true;
        addTick(bottomGuideX, bottomStartY, 'v');
        addTick(bottomGuideX, bottomEndY, 'v');
        addBubble(verticalBubbleX(bottomGuideX), (bottomStartY + bottomEndY) / 2, bottomGap, 'gap-v');
      }
    }

    try{
      console.log('[preview] drawSideConnectors coords', {
        A:{left:A.left,top:A.top,w:A.width,h:A.height},
        B:{left:B.left,top:B.top,w:B.width,h:B.height},
        overlapW, overlapH, xGap, yGap,
        xMode, yMode, drewX, drewY,
        xContains,yContains,
        containerRectLeft: containerRect.left, scale
      });
    }catch(e){}
  }

  function attachList(nodes, doc, scale, meta){
    layerList.innerHTML='';
    clearOverlay();
    clearGaps();
    const frameWrap = document.getElementById('frame-wrap');
    // 始终以 frame-wrap 作为坐标基准，避免嵌入文档容器 rect 为 0 导致整体偏移。
    const containerRect = frameWrap.getBoundingClientRect();

    const sidebarEl = document.getElementById('sidebar');
    const sidebarVisible = sidebarEl && getComputedStyle(sidebarEl).display !== 'none';

    // 遍历一次，构建 overlay 元素并绑定交互（overlay-only 模式下不构建侧栏）
    nodes.forEach((el)=>{
      let rect;
      try{ rect = el.getBoundingClientRect(); }catch(e){ return; }
      const name = getNameFor(el);
      const {box} = makeBoxFor(rect, name, scale, containerRect);

        // 鼠标悬停：显示该层的 box，并在存在选中目标时展示 gap 与连线
        box.addEventListener('mouseenter', ()=>{
          box.classList.add('visible');
          try{ console.log('[preview] box mouseenter', name); }catch(e){}
          if(selectedMeta && selectedMeta.el !== el){
            try{ showGaps(selectedMeta.rect, rect, containerRect, scale); }catch(e){}
          }
        });
        box.addEventListener('mouseleave', ()=>{
          // 如果当前为选中元素，保持 selected 状态可见
          if(!(selectedMeta && selectedMeta.el === el)){
            box.classList.remove('visible');
          }
          clearGaps(); clearConnector();
        });

        // 点击：设为选中并保持可见（并在右侧面板高亮）
        box.addEventListener('click', (ev)=>{
          ev.stopPropagation();
          ev.preventDefault();
          try{ console.log('[preview] box click', name); }catch(e){}
          setSelected({el, rect, containerRect, scale, box, name});
          clearGaps();
        });

      // 不在列表中自动填充；详情由选中时显示（减少干扰）
      if(infoListPanel){
        // store name on box for later detail display
        box.dataset.infoName = name;
        box.dataset.left = Math.round(rect.left);
        box.dataset.top = Math.round(rect.top);
        box.dataset.width = Math.round(rect.width);
        box.dataset.height = Math.round(rect.height);
      }
    });

    // 点击 overlay 空白区域可取消选中
    overlay.addEventListener('click', (ev)=>{ if(ev.target === overlay){ clearSelected(); clearGaps(); clearConnector(); } });

    toggleOutline.checked ? overlay.style.display='block' : overlay.style.display='none';
  }

  function updateScale(s){
    // 缩放 overlay 大小以匹配容器内容
    // 当传入 meta 时，优先使用 containerRect 来设置 overlay 大小
    let frameRect;
    if(arguments.length>1 && arguments[1] && arguments[1].containerRect){
      frameRect = arguments[1].containerRect;
    }else{
      const frameWrap = document.getElementById('frame-wrap');
      frameRect = frameWrap.getBoundingClientRect();
    }
    // 不使用 CSS scale 在 overlay 上（避免重复缩放），直接根据 scale 调整宽高
    overlay.style.width = (frameRect.width * s) + 'px';
    overlay.style.height = (frameRect.height * s) + 'px';
    overlay.style.transform = '';
  }

  function init(){
    function computeDocSize(doc){
      const de = doc && doc.documentElement ? doc.documentElement : null;
      const bd = doc && doc.body ? doc.body : null;
      const width = Math.max(
        de ? de.scrollWidth : 0,
        bd ? bd.scrollWidth : 0,
        de ? de.clientWidth : 0,
        bd ? bd.clientWidth : 0,
        1
      );
      const height = Math.max(
        de ? de.scrollHeight : 0,
        bd ? bd.scrollHeight : 0,
        de ? de.clientHeight : 0,
        bd ? bd.clientHeight : 0,
        1
      );
      return {width, height};
    }

    function onDocReady(doc){
        const nodes = findLayerNodes(doc);
        console.log('[preview] found nodes:', nodes.length);
        if(nodes.length>0){
          try{ console.log('[preview] first node rect sample', nodes[0].getBoundingClientRect()); }catch(e){}
        }
      const scale = parseFloat(scaleInput.value) || 1;
        // 按被预览文档实际高度扩展 iframe，使超过一屏的内容可滚动查看
        const frameWrap = document.getElementById('frame-wrap');
        const docSize = computeDocSize(doc);
        try{
          frame.style.width = docSize.width + 'px';
          frame.style.height = docSize.height + 'px';
          frameWrap.style.minHeight = docSize.height + 'px';
        }catch(e){}

        const frameRect = frameWrap.getBoundingClientRect();
        const containerRect = {
          left: frameRect.left,
          top: frameRect.top,
          right: frameRect.left + docSize.width,
          bottom: frameRect.top + docSize.height,
          width: docSize.width,
          height: docSize.height,
          x: frameRect.left,
          y: frameRect.top
        };
        const offsetX = containerRect.left - frameRect.left;
        const offsetY = containerRect.top - frameRect.top;
        // 设置 overlay 大小与偏移，使其覆盖容器区域
        overlay.style.left = offsetX + 'px';
        overlay.style.top = offsetY + 'px';
        overlay.style.width = containerRect.width + 'px';
        overlay.style.height = containerRect.height + 'px';
        console.log('[preview] containerRect', containerRect, 'frameRect', frameRect, 'offset', offsetX, offsetY);
        attachList(nodes, doc, scale, {offsetX, offsetY, containerRect: frameRect, frameRect});
        updateScale(scale, {containerRect, frameRect});
    }

    // 优先：如果安装脚本把 index.html 内嵌为文本节点（避免 file:// 同源问题），
    // 使用 iframe.srcdoc 加载该完整页面内容，这比把整个 HTML 放入一个 div 更可靠。
    const embedded = document.getElementById('__embedded_index');
    if(embedded && embedded.textContent && embedded.textContent.trim()){
      try{
        // make iframe visible and load srcdoc
        frame.style.display = '';
        frame.srcdoc = embedded.textContent;
        frame.addEventListener('load', ()=>{
          try{
            const doc = frame.contentDocument || frame.contentWindow.document;
            // If iframe document exists but appears empty, fallback to injecting into embedded-root
            const body = doc && doc.body;
            if(!body || (body.children && body.children.length===0) || (body.innerHTML && body.innerHTML.trim().length===0)){
              const frameWrap = document.getElementById('frame-wrap');
              const wrapper = document.getElementById('embedded-root') || document.createElement('div');
              wrapper.id = 'embedded-root';
              wrapper.innerHTML = embedded.textContent;
              if(!document.getElementById('embedded-root')) frameWrap.appendChild(wrapper);
              onDocReady(wrapper);
              return;
            }
            onDocReady(doc);
          }catch(e){
            // fallback: if iframe can't be accessed, inject into embedded-root
            const frameWrap = document.getElementById('frame-wrap');
            const wrapper = document.getElementById('embedded-root') || document.createElement('div');
            wrapper.id = 'embedded-root';
            wrapper.innerHTML = embedded.textContent;
            if(!document.getElementById('embedded-root')) frameWrap.appendChild(wrapper);
            onDocReady(wrapper);
          }
        }, {once:true});
        return;
      }catch(e){ /* continue to fetch fallback below */ }
    }

    // 尝试 fetch index.html 并用 srcdoc 加载
    fetch('index.html').then(r=>r.text()).then(html=>{
      try{
        frame.srcdoc = html;
        frame.addEventListener('load', ()=>{
          const doc = frame.contentDocument || frame.contentWindow.document;
          onDocReady(doc);
        }, {once:true});
      }catch(e){
        info.textContent = '无法通过 srcdoc 加载 index.html：' + e;
      }
    }).catch(()=>{
      // 回退：尝试直接访问 iframe（可能会触发跨域错误）
      frame.addEventListener('load', ()=>{
        let doc;
        try{doc = frame.contentDocument || frame.contentWindow.document; onDocReady(doc);}catch(e){
          info.textContent = '无法访问 iframe 文档（跨域或 file:// 限制）。若在本地请使用 run_and_install 将预览嵌入。';
        }
      }, {once:true});
      frame.src = 'index.html';
    });

    scaleInput.addEventListener('input', ()=>{
      const s = parseFloat(scaleInput.value);
      updateScale(s);
      try{
        const doc = frame.contentDocument||frame.contentWindow.document;
        const nodes = findLayerNodes(doc);
        const frameWrap = document.getElementById('frame-wrap');
        const docSize = computeDocSize(doc);
        try{
          frame.style.width = docSize.width + 'px';
          frame.style.height = docSize.height + 'px';
          frameWrap.style.minHeight = docSize.height + 'px';
        }catch(e){}
        const frameRect = frameWrap.getBoundingClientRect();
        const containerRect = {
          left: frameRect.left,
          top: frameRect.top,
          right: frameRect.left + docSize.width,
          bottom: frameRect.top + docSize.height,
          width: docSize.width,
          height: docSize.height,
          x: frameRect.left,
          y: frameRect.top
        };
        const offsetX = containerRect.left - frameRect.left;
        const offsetY = containerRect.top - frameRect.top;
        attachList(nodes, doc, s, {offsetX, offsetY, containerRect, frameRect});
      }catch(e){
        // 尝试从嵌入容器
        const embeddedRoot = document.getElementById('embedded-root');
        if(embeddedRoot && embeddedRoot.children && embeddedRoot.children.length>0){
          const nodes = findLayerNodes(embeddedRoot);
          const frameWrap = document.getElementById('frame-wrap');
          const frameRect = frameWrap.getBoundingClientRect();
          const containerRect = frameRect;
          const offsetX = containerRect.left - frameRect.left;
          const offsetY = containerRect.top - frameRect.top;
          attachList(nodes, embeddedRoot, s, {offsetX, offsetY, containerRect: frameRect, frameRect});
        }
      }
    });

    toggleOutline.addEventListener('change', ()=>{overlay.style.display = toggleOutline.checked ? 'block':'none'});
  }

  document.addEventListener('DOMContentLoaded', init);

})();
