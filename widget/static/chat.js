/* static/chat.js
   Plain JS (no CDN), no fetch (XHR).
*/
(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function getQueryParam(name) {
    var qs = window.location.search || "";
    if (qs.indexOf("?") === 0) qs = qs.substring(1);
    var parts = qs.split("&");
    for (var i = 0; i < parts.length; i++) {
      var kv = parts[i].split("=");
      if (decodeURIComponent(kv[0] || "") === name) {
        return decodeURIComponent(kv[1] || "");
      }
    }
    return "";
  }

  var widgetKey = getQueryParam("key") || window.__WIDGET_KEY__ || "demo";
  var storageKey = "ws_session_" + widgetKey;

  var messagesEl = $("wsMessages");
  var inputEl = $("wsInput");
  var sendEl = $("wsSend");
  var closeBtn = $("wsCloseBtn");

  var sessionId = "";
  var pageUrl = "";
  var pageTitle = "";
  var pending = false;
  var typingMessageEl = null;
  
  // Инициализируем pageUrl из window.location если referrer пустой (для iframe)
  try {
    pageUrl = document.referrer || window.location.href || "";
  } catch (e) {
    pageUrl = "";
  }

  function parseProducts(text) {
    var products = [];
    var lines = text.split("\n");
    var currentCategory = null;
    var i = 0;
    
    while (i < lines.length) {
      var line = lines[i].trim();
      
      // Определяем категорию товаров (формат: **Категория:** или просто "Категория")
      if (line.indexOf("**") === 0 && line.lastIndexOf("**") > 0) {
        var categoryMatch = line.match(/\*\*([^*]+)\*\*/);
        if (categoryMatch && categoryMatch[1]) {
          currentCategory = categoryMatch[1].replace(":", "").trim();
          i++;
          continue;
        }
      }
      
      // Определяем категорию без звездочек (просто текст)
      // Категория определяется если:
      // 1. Строка короткая (до 50 символов)
      // 2. Не содержит маркеры товаров/цен
      // 3. Следующая строка - название товара, а через строку - цена
      if (line.length > 0 && line.length < 50 && 
          line.indexOf("💰") === -1 && line.indexOf("•") === -1 && 
          line.indexOf("🛒") === -1 && !/^\d/.test(line) && 
          line.indexOf("RUB") === -1 && line.indexOf("руб") === -1 &&
          line.indexOf("Цена") === -1 && line.indexOf("**") === -1) {
        
        // Проверяем паттерн: категория -> название товара -> цена
        if (i + 2 < lines.length) {
          var nextLine = lines[i + 1] ? lines[i + 1].trim() : "";
          var nextNextLine = lines[i + 2] ? lines[i + 2].trim() : "";
          
          // Если следующая строка - название товара (не содержит маркеры), а через строку - цена
          if (nextLine && nextLine.length > 2 && 
              nextLine.indexOf("💰") === -1 && nextLine.indexOf("•") === -1 && 
              nextLine.indexOf("RUB") === -1 && !/^\d/.test(nextLine) &&
              (nextNextLine.indexOf("💰") !== -1 || 
               (nextNextLine.indexOf("Цена") !== -1 && (nextNextLine.indexOf("RUB") !== -1 || /\d/.test(nextNextLine))))) {
            // Это категория!
            currentCategory = line;
            i++;
            continue;
          }
        }
      }
      
      // Парсим товар в формате "• Название — Цена"
      // Также поддерживаем формат с длинным тире (—) и обычным дефисом (-)
      if (line.indexOf("•") === 0) {
        // Проверяем наличие тире (длинное или обычное)
        var dashIndex = line.indexOf("—");
        if (dashIndex === -1) {
          dashIndex = line.indexOf("-");
        }
        
        if (dashIndex !== -1) {
          var name = line.substring(1, dashIndex).trim();
          var price = line.substring(dashIndex + 1).trim();
          
          // Убираем длинное тире из цены если есть
          price = price.replace(/^[—\-]+/, "").trim();
          
          if (name && price && name.length > 2) {
            products.push({
              name: name,
              price: price,
              category: currentCategory || "Товары"
            });
          }
        }
        i++;
        continue;
      }
      
      // Парсим товар в формате с эмодзи 💰 (многострочный формат: название на одной строке, цена на следующей)
      // Формат:
      // Название товара
      // 💰 Цена X RUB
      if (line.indexOf("💰") !== -1 || (line.indexOf("Цена") !== -1 && (line.indexOf("RUB") !== -1 || /\d/.test(line)))) {
        // Это строка с ценой, нужно найти название товара выше
        if (i > 0) {
          var prevLine = lines[i - 1] ? lines[i - 1].trim() : "";
          // Если предыдущая строка не пустая и не категория и не содержит маркеры
          if (prevLine && prevLine.length > 2 && 
              prevLine.indexOf("💰") === -1 && prevLine.indexOf("•") === -1 && 
              prevLine.indexOf("🛒") === -1 && prevLine.indexOf("**") === -1 &&
              prevLine !== currentCategory && !/^\d/.test(prevLine) &&
              prevLine.indexOf("RUB") === -1 && prevLine.indexOf("руб") === -1 &&
              prevLine.indexOf("Цена") === -1) {
            
            // Извлекаем цену из текущей строки
            var priceMatch = line.match(/(\d+)\s*(RUB|руб|₽|рублей)/i);
            if (!priceMatch) {
              // Пробуем найти число в строке
              priceMatch = line.match(/(\d+)/);
            }
            
            if (priceMatch) {
              var productPrice = priceMatch[1] + " " + (priceMatch[2] || "RUB");
              products.push({
                name: prevLine,
                price: productPrice,
                category: currentCategory || "Товары"
              });
              console.log("[Widget] Parsed product:", prevLine, "->", productPrice, "category:", currentCategory || "Товары");
              i++;
              continue;
            }
          }
        }
      }
      
      // Парсим товар в формате с эмодзи 🛒 (многострочный формат)
      // Формат может быть:
      // 🛒
      // Категория
      // Название товара
      // Цена RUB
      // ИЛИ
      // Категория
      // 🛒
      // Категория (повтор)
      // Название товара
      // Цена RUB
      if (line === "🛒" || (line.indexOf("🛒") !== -1 && line.length < 5)) {
        var savedCategory = currentCategory;
        
        // Пропускаем строку с эмодзи
        i++;
        if (i >= lines.length) break;
        
        // Следующая строка может быть категорией
        var categoryLine = lines[i] ? lines[i].trim() : "";
        if (categoryLine && categoryLine.length > 0 && categoryLine.indexOf("RUB") === -1 && !/^\d/.test(categoryLine) && categoryLine !== "🛒") {
          currentCategory = categoryLine;
          i++;
        }
        
        // Если следующая строка тоже категория (повтор), пропускаем
        if (i < lines.length) {
          var nextLine = lines[i] ? lines[i].trim() : "";
          if (nextLine === currentCategory || (nextLine.length > 0 && nextLine.indexOf("RUB") === -1 && !/^\d/.test(nextLine) && nextLine !== "🛒")) {
            // Может быть повтор категории или еще одна категория
            if (nextLine !== currentCategory && nextLine.length > 0) {
              currentCategory = nextLine;
            }
            i++;
          }
        }
        
        // Теперь ищем название товара и цену
        while (i < lines.length) {
          var nameLine = lines[i] ? lines[i].trim() : "";
          if (!nameLine || nameLine === "🛒" || nameLine === currentCategory) {
            i++;
            continue;
          }
          
          // Проверяем, содержит ли строка цену
          var priceMatch = nameLine.match(/(\d+)\s*(RUB|руб|₽|RUB|рублей)/i);
          if (priceMatch) {
            // Цена найдена в этой строке
            var productName = nameLine.replace(priceMatch[0], "").trim();
            var productPrice = priceMatch[1] + " " + (priceMatch[2] || "RUB");
            
            if (productName && productName.length > 2) {
              products.push({
                name: productName,
                price: productPrice,
                category: currentCategory || savedCategory || "Товары"
              });
            }
            i++;
            break; // Переходим к следующему товару
          } else {
            // Возможно, название в этой строке, а цена в следующей
            var productName = nameLine;
            i++;
            if (i < lines.length) {
              var priceLine = lines[i] ? lines[i].trim() : "";
              var priceMatch2 = priceLine.match(/(\d+)\s*(RUB|руб|₽|RUB|рублей)/i);
              if (priceMatch2) {
                var productPrice = priceMatch2[1] + " " + (priceMatch2[2] || "RUB");
                if (productName && productName.length > 2) {
                  products.push({
                    name: productName,
                    price: productPrice,
                    category: currentCategory || savedCategory || "Товары"
                  });
                }
                i++;
                break;
              } else {
                // Если следующая строка не цена, возможно это начало нового товара
                break;
              }
            } else {
              break;
            }
          }
        }
        continue;
      }
      
      i++;
    }
    
    return products;
  }

  function createProductCard(product) {
    var card = document.createElement("div");
    card.className = "ws-product-card";
    card.setAttribute("data-product", product.name);
    
    var title = document.createElement("div");
    title.className = "ws-product-title";
    title.textContent = product.name;
    
    var priceContainer = document.createElement("div");
    priceContainer.className = "ws-product-price";
    
    // Форматируем цену - добавляем эмодзи и создаем badge
    var priceText = product.price.trim();
    // Убираем слово "Цена" если есть, оставляем только число и валюту
    priceText = priceText.replace(/Цена\s*/gi, "").trim();
    // Добавляем эмодзи если его нет
    if (priceText.indexOf("💰") === -1) {
      priceText = "💰 Цена " + priceText;
    }
    
    var priceBadge = document.createElement("span");
    priceBadge.className = "ws-product-price-badge";
    priceBadge.textContent = priceText;
    
    priceContainer.appendChild(priceBadge);
    
    // Кнопка добавления в корзину
    var addToCartBtn = document.createElement("button");
    addToCartBtn.className = "ws-product-add-to-cart";
    // Сохраняем данные товара на кнопке
    addToCartBtn.productData = product;
    addToCartBtn.isInCart = false; // Будет обновляться при проверке состояния
    
    // Функция для обновления состояния кнопки
    function updateButtonState() {
      if (addToCartBtn.isInCart) {
        addToCartBtn.textContent = "✓ Добавлено";
        addToCartBtn.classList.add("added");
      } else {
        addToCartBtn.textContent = "➕ В корзину";
        addToCartBtn.classList.remove("added");
      }
    }
    
    // Инициализируем состояние кнопки
    updateButtonState();
    
    // Запрашиваем состояние корзины у родительской страницы
    try {
      window.parent.postMessage({
        type: "CHECK_CART_STATUS",
        product: {
          name: product.name,
          price: product.price
        }
      }, "*");
    } catch (err) {
      console.error("Failed to check cart status:", err);
    }
    
    addToCartBtn.onclick = function(e) {
      e.stopPropagation();
      // Предотвращаем множественные клики
      if (addToCartBtn.disabled) return;
      addToCartBtn.disabled = true;
      
      var isCurrentlyInCart = addToCartBtn.isInCart;
      
      // Отправляем сообщение родительской странице
      try {
        if (isCurrentlyInCart) {
          // Удаляем из корзины
          window.parent.postMessage({
            type: "REMOVE_FROM_CART",
            product: {
              name: product.name,
              price: product.price,
              category: product.category || "Товары"
            }
          }, "*");
        } else {
          // Добавляем в корзину
          window.parent.postMessage({
            type: "ADD_TO_CART",
            product: {
              name: product.name,
              price: product.price,
              category: product.category || "Товары"
            }
          }, "*");
        }
        
        // Обновляем состояние кнопки сразу
        addToCartBtn.isInCart = !isCurrentlyInCart;
        updateButtonState();
        
        // Разблокируем кнопку после небольшой задержки
        setTimeout(function() {
          addToCartBtn.disabled = false;
        }, 300);
      } catch (err) {
        console.error("Failed to send message to parent:", err);
        addToCartBtn.disabled = false;
      }
    };
    
    // Обработчик сообщений от родительской страницы
    window.addEventListener("message", function(event) {
      if (event.data && event.data.type === "CART_STATUS_RESPONSE") {
        if (event.data.product && 
            event.data.product.name === product.name && 
            event.data.product.price === product.price) {
          addToCartBtn.isInCart = event.data.inCart;
          updateButtonState();
        }
      }
      
      if (event.data && event.data.type === "CART_UPDATED") {
        // Проверяем состояние товара после обновления корзины
        if (event.data.product && 
            event.data.product.name === product.name && 
            event.data.product.price === product.price) {
          addToCartBtn.isInCart = event.data.inCart;
          updateButtonState();
        }
      }
    });
    
    card.appendChild(title);
    card.appendChild(priceContainer);
    card.appendChild(addToCartBtn);
    
    // Добавляем обработчик клика на карточку (не на кнопку)
    card.onclick = function(e) {
      if (e.target === addToCartBtn || e.target.closest(".ws-product-add-to-cart")) {
        return;
      }
      console.log("Product clicked:", product.name);
    };
    
    return card;
  }

  function createProductsCarousel(products, categoryTitle) {
    if (!products || products.length === 0) return null;
    
    var section = document.createElement("div");
    section.className = "ws-products-section";
    
    // Заголовок категории (как в Streamlit)
    var categoryHeader = document.createElement("div");
    categoryHeader.className = "ws-product-category-header";
    categoryHeader.textContent = categoryTitle || "Товары";
    
    var carousel = document.createElement("div");
    carousel.className = "ws-products-carousel";
    
    for (var i = 0; i < products.length; i++) {
      var card = createProductCard(products[i]);
      carousel.appendChild(card);
    }
    
    section.appendChild(categoryHeader);
    section.appendChild(carousel);
    
    return section;
  }

  function escapeHtml(text) {
    if (!text) return "";
    var map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;"
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
  }

  function markdownToHtml(text) {
    if (!text) return "";
    
    // Экранируем HTML для безопасности
    var html = escapeHtml(text);
    
    // Сначала обрабатываем двойные звездочки **текст** -> <strong>текст</strong>
    // Используем нежадный поиск для правильной обработки нескольких вхождений
    html = html.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
    
    // Затем обрабатываем одинарные звездочки *текст* -> <em>текст</em>
    // Только если это не часть двойных звездочек (уже обработано выше)
    html = html.replace(/\*([^*\n]+?)\*/g, "<em>$1</em>");
    
    // Преобразуем переносы строк в <br>
    html = html.replace(/\n/g, "<br>");
    
    return html;
  }

  function addMessage(role, text) {
    var row = document.createElement("div");
    row.className = "ws-row " + role;

    var bubble = document.createElement("div");
    bubble.className = "ws-bubble";
    
    // Парсим товары из текста
    var products = parseProducts(text);
    
    // Отладочная информация
    if (products.length > 0) {
      console.log("[Widget] Found", products.length, "products:", products);
    } else {
      console.log("[Widget] No products found. Text preview:", text.substring(0, 200));
      if (text.indexOf("•") !== -1) {
        console.log("[Widget] Text contains bullet points but products not parsed");
      }
    }
    
    if (products.length > 0 && role === "bot") {
      // Разделяем текст на части: до товаров и товары
      // Ищем начало товаров (🛒 или •)
      var textBeforeProducts = text;
      var productStartIndex = -1;
      
      // Ищем начало блока с товарами
      var productMarkers = [
        "\n🛒",
        "\n•"
      ];
      
      for (var m = 0; m < productMarkers.length; m++) {
        var markerIndex = text.indexOf(productMarkers[m]);
        if (markerIndex !== -1) {
          productStartIndex = markerIndex;
          textBeforeProducts = text.substring(0, markerIndex).trim();
          break;
        }
      }
      
      // Если не нашли маркер, но есть товары, удаляем их из текста
      if (productStartIndex === -1 && products.length > 0) {
        // Пытаемся найти начало первого товара
        var firstProductName = products[0].name;
        var nameIndex = text.indexOf("• " + firstProductName);
        if (nameIndex === -1) {
          nameIndex = text.indexOf(firstProductName);
        }
        if (nameIndex !== -1) {
          // Ищем начало строки с товаром
          var lineStart = text.lastIndexOf("\n", nameIndex);
          if (lineStart === -1) lineStart = 0;
          // Также проверяем, есть ли перед этим заголовок категории
          var beforeLine = text.substring(Math.max(0, lineStart - 50), lineStart);
          if (beforeLine.indexOf("**") !== -1) {
            var categoryStart = text.lastIndexOf("**", lineStart);
            if (categoryStart !== -1) {
              lineStart = categoryStart;
            }
          }
          textBeforeProducts = text.substring(0, lineStart).trim();
        }
      }
      
      // Создаем контейнер для контента
      var contentDiv = document.createElement("div");
      contentDiv.className = "ws-bubble-content";
      
      // Добавляем текстовую часть с markdown форматированием
      if (textBeforeProducts.trim()) {
        var textNode = document.createElement("div");
        textNode.style.whiteSpace = "pre-wrap";
        textNode.style.marginBottom = products.length > 0 ? "12px" : "0";
        textNode.innerHTML = markdownToHtml(textBeforeProducts.trim());
        contentDiv.appendChild(textNode);
      }
      
      // Группируем товары по категориям
      var productsByCategory = {};
      for (var i = 0; i < products.length; i++) {
        var cat = products[i].category;
        if (!productsByCategory[cat]) {
          productsByCategory[cat] = [];
        }
        productsByCategory[cat].push(products[i]);
      }
      
      // Добавляем карусели для каждой категории
      for (var cat in productsByCategory) {
        var carousel = createProductsCarousel(productsByCategory[cat], cat);
        if (carousel) {
          console.log("[Widget] Adding carousel for category:", cat, "with", productsByCategory[cat].length, "products");
          contentDiv.appendChild(carousel);
        }
      }
      
      bubble.appendChild(contentDiv);
      console.log("[Widget] Message with products added, total products:", products.length);
    } else {
      // Обычное текстовое сообщение с markdown форматированием
      var textContent = document.createElement("div");
      textContent.style.whiteSpace = "pre-wrap";
      textContent.innerHTML = markdownToHtml(text);
      bubble.appendChild(textContent);
    }

    row.appendChild(bubble);
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return row;
  }

  function showTypingIndicator(statusText) {
    if (typingMessageEl) {
      removeTypingIndicator();
    }
    
    var row = document.createElement("div");
    row.className = "ws-row typing";
    row.id = "wsTypingIndicator";

    var bubble = document.createElement("div");
    bubble.className = "ws-bubble";
    
    var typingIndicator = document.createElement("div");
    typingIndicator.className = "ws-typing-indicator";
    
    for (var i = 0; i < 3; i++) {
      var dot = document.createElement("div");
      dot.className = "ws-typing-dot";
      typingIndicator.appendChild(dot);
    }
    
    var statusSpan = document.createElement("span");
    statusSpan.textContent = statusText || "Обрабатываю запрос...";
    
    bubble.appendChild(typingIndicator);
    bubble.appendChild(statusSpan);
    row.appendChild(bubble);
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    
    typingMessageEl = row;
  }

  function removeTypingIndicator() {
    if (typingMessageEl) {
      typingMessageEl.remove();
      typingMessageEl = null;
    }
  }

  function updateTypingStatus(statusText) {
    if (typingMessageEl) {
      var statusSpan = typingMessageEl.querySelector(".ws-bubble span");
      if (statusSpan) {
        statusSpan.textContent = statusText;
      }
    } else {
      showTypingIndicator(statusText);
    }
  }

  function setPending(v) {
    pending = v;
    sendEl.disabled = !!v;
  }

  function postJSON(url, obj, cb) {
    var xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      var ok = xhr.status >= 200 && xhr.status < 300;
      var data = null;
      try {
        data = JSON.parse(xhr.responseText || "{}");
      } catch (e) {
        data = { reply: xhr.responseText || "" };
      }
      cb(ok, xhr.status, data);
    };
    xhr.send(JSON.stringify(obj));
  }

  function ensureSession(cb, forceNew) {
    // Если уже есть sessionId и не требуем новую — используем (только из памяти, НЕ из localStorage!)
    if (!forceNew && sessionId && sessionId.length > 0) {
      cb(true);
      return;
    }

    // Никогда не восстанавливаем из localStorage — после перезапуска Docker сессии теряются
    if (forceNew) {
      sessionId = "";
      try {
        window.localStorage.removeItem(storageKey);
      } catch (e) {}
    }

    // Обновляем pageUrl перед созданием сессии
    try {
      if (!pageUrl || pageUrl.length === 0) {
        // Пробуем получить URL из разных источников
        pageUrl = document.referrer || window.location.href || "";
        try {
          pageUrl = pageUrl || window.parent.location.href || "";
        } catch (e) {
          // Не можем получить parent.location (cross-origin)
        }
      }
    } catch (e) {
      // Если не можем получить URL, используем пустую строку
      pageUrl = "";
    }

    var payload = { widget_key: widgetKey, page_url: pageUrl };
    postJSON("/api/session", payload, function (ok, status, data) {
      if (!ok || !data || !data.session_id) {
        var errorMsg = "Не удалось создать сессию (" + status + ")";
        if (data && data.detail) {
          if (typeof data.detail === "string") {
            errorMsg += ": " + data.detail;
          } else if (data.detail.error) {
            errorMsg += ": " + data.detail.error;
          } else {
            errorMsg += ": " + JSON.stringify(data.detail);
          }
        }
        addMessage("sys", errorMsg);
        cb(false);
        return;
      }
      sessionId = String(data.session_id);
      if (!sessionId || sessionId.length === 0) {
        addMessage("sys", "Ошибка: получен пустой session_id");
        cb(false);
        return;
      }
      try {
        window.localStorage.setItem(storageKey, sessionId);
      } catch (e) {
        // Если localStorage недоступен, продолжаем без него
        // Сессия все равно будет работать в рамках текущей сессии браузера
      }
      cb(true);
    });
  }

  function sendMessage() {
    var text = (inputEl.value || "").replace(/^\s+|\s+$/g, "");
    if (!text || pending) return;

    addMessage("user", text);
    inputEl.value = "";
    setPending(true);

    // Показываем индикатор обработки
    showTypingIndicator("🔍 Анализирую запрос...");

    ensureSession(function (ok) {
      if (!ok) {
        setPending(false);
        removeTypingIndicator();
        addMessage("sys", "Не удалось создать сессию. Попробуйте перезагрузить страницу.");
        return;
      }

      // Проверяем, что sessionId установлен
      if (!sessionId || sessionId.length === 0) {
        addMessage("sys", "Ошибка: сессия не создана. Попробуйте перезагрузить виджет.");
        setPending(false);
        removeTypingIndicator();
        return;
      }

      var payload = {
        session_id: sessionId,
        message: text,
        widget_key: widgetKey,
        context: {
          page_url: pageUrl,
          page_title: pageTitle
        }
      };

      // Обновляем статус через небольшие интервалы для имитации прогресса
      var statusMessages = [
        "🔍 Анализирую запрос...",
        "🔎 Ищу релевантные товары...",
        "🤖 Генерирую ответ..."
      ];
      var statusIndex = 0;
      var statusInterval = setInterval(function() {
        if (statusIndex < statusMessages.length) {
          updateTypingStatus(statusMessages[statusIndex]);
          statusIndex++;
        }
      }, 1500);

      postJSON("/api/chat", payload, function (ok2, status2, data2) {
        clearInterval(statusInterval);
        removeTypingIndicator();
        
        if (!ok2) {
          // Проверяем, является ли это ошибкой "unknown_session"
          var isUnknownSession = false;
          if (status2 === 400) {
            if (data2 && data2.detail) {
              if (typeof data2.detail === "string") {
                isUnknownSession = data2.detail.indexOf("unknown_session") !== -1;
              } else if (typeof data2.detail === "object") {
                isUnknownSession = data2.detail.error === "unknown_session" || 
                                   (data2.detail.detail && typeof data2.detail.detail === "string" && 
                                    data2.detail.detail.indexOf("unknown_session") !== -1);
              }
            }
          }
          
          // Если ошибка "unknown_session", создаем новую сессию и повторяем запрос
          if (isUnknownSession) {
            // Сессия не найдена, создаем новую (принудительно)
            sessionId = "";
            try {
              window.localStorage.removeItem(storageKey);
            } catch (e) {}
            
            // Принудительно создаем новую сессию (forceNew=true)
            ensureSession(function(ok3) {
              if (!ok3) {
                setPending(false);
                addMessage("sys", "Не удалось создать новую сессию. Попробуйте перезагрузить страницу.");
                return;
              }
              
              // Повторяем запрос с новой сессией (используем оригинальный текст сообщения)
              var retryPayload = {
                session_id: sessionId,
                message: text,
                widget_key: widgetKey,
                context: {
                  page_url: pageUrl,
                  page_title: pageTitle
                }
              };
              
              showTypingIndicator("🔍 Анализирую запрос...");
              var retryStatusIndex = 0;
              var retryStatusInterval = setInterval(function() {
                if (retryStatusIndex < statusMessages.length) {
                  updateTypingStatus(statusMessages[retryStatusIndex]);
                  retryStatusIndex++;
                }
              }, 1500);
              
              postJSON("/api/chat", retryPayload, function (ok4, status4, data4) {
                clearInterval(retryStatusInterval);
                removeTypingIndicator();
                setPending(false);
                
                if (!ok4) {
                  addMessage("sys", "Ошибка (" + status4 + "): " + (data4 && data4.detail ? JSON.stringify(data4.detail) : "request failed"));
                  return;
                }
                if (data4 && data4.session_id && typeof data4.session_id === "string") {
                  sessionId = data4.session_id;
                  try { window.localStorage.setItem(storageKey, sessionId); } catch (e) {}
                }
                var reply = "";
                if (data4 && typeof data4.reply !== "undefined") reply = String(data4.reply);
                else reply = JSON.stringify(data4);

                addMessage("bot", reply);
              });
            }, true); // forceNew=true для принудительного создания новой сессии
            return;
          }
          
          setPending(false);
          var errorMsg = "Ошибка (" + status2 + ")";
          if (data2 && data2.detail) {
            if (typeof data2.detail === "string") {
              errorMsg += ": " + data2.detail;
            } else if (typeof data2.detail === "object") {
              errorMsg += ": " + JSON.stringify(data2.detail);
            }
          } else {
            errorMsg += ": request failed";
          }
          addMessage("sys", errorMsg);
          return;
        }
        
        setPending(false);
        // Если сервер вернул новый session_id (после автосоздания) — обновляем
        if (data2 && data2.session_id && typeof data2.session_id === "string") {
          sessionId = data2.session_id;
          try { window.localStorage.setItem(storageKey, sessionId); } catch (e) {}
        }
        var reply = "";
        if (data2 && typeof data2.reply !== "undefined") reply = String(data2.reply);
        else reply = JSON.stringify(data2);

        addMessage("bot", reply);
      });
    });
  }

  function closeWidget() {
    try {
      window.parent.postMessage({ type: "WIDGET_CLOSE" }, "*");
    } catch (e) {}
  }

  function onMessage(ev) {
    if (!ev || !ev.data) return;
    var d = ev.data;
    if (d.type === "WIDGET_CONTEXT") {
      var needNewSession = false;
      if (d.page_url) {
        var newPageUrl = String(d.page_url);
        if (newPageUrl !== pageUrl) {
          pageUrl = newPageUrl;
          needNewSession = true; // Если URL изменился, создаем новую сессию
        }
      }
      if (d.page_title) {
        pageTitle = String(d.page_title);
      }
      // Если получили контекст, но сессия еще не создана или URL изменился, создаем сессию
      if ((!sessionId || needNewSession) && pageUrl) {
        sessionId = ""; // Сбрасываем старую сессию
        ensureSession(function(ok) {
          // Сессия создана или не удалось создать
        });
      }
      return;
    }
  }

  if (window.addEventListener) {
    window.addEventListener("message", onMessage, false);
  } else if (window.attachEvent) {
    window.attachEvent("onmessage", onMessage);
  }

  try {
    window.parent.postMessage({ type: "WIDGET_READY" }, "*");
  } catch (e) {}
  
  // Всегда создаем новую сессию при загрузке виджета (не используем localStorage)
  // Это гарантирует валидную сессию после перезапуска сервера/Docker
  ensureSession(function(ok) {
    // Сессия создана или будет создана при первом сообщении
  }, true);

  sendEl.onclick = function () {
    sendMessage();
  };

  inputEl.onkeydown = function (e) {
    e = e || window.event;
    var key = e.keyCode || e.which;
    if (key === 13) {
      sendMessage();
      return false;
    }
    return true;
  };

  closeBtn.onclick = function () {
    closeWidget();
  };

  addMessage("bot", "Напишите, что хотите подобрать — я уточню детали и предложу варианты из каталога.");

})();
