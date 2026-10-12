# NetSuite Read-only Entegrasyon Tasarımı

Bu belge mevcut ve onaylanmış kapsamı tanımlar. Entegrasyon NetSuite'e kayıt yazmaz; Demand Plan, Work Order, Transfer Order, gelecek arz, eski SKU eşleme ve alternatif tahmin modelleri bu sürümde yoktur.

## Veri akışı

```text
NetSuite REST SuiteQL (read-only)
        |
        | ürün/beden + satış/iade + satılabilir stok
        v
Kanonik veri doğrulaması
        |
        v
Eş dönem net satış × büyüme çarpanı
        |
        v
Üretim ihtiyacı + üç depo alokasyonu
        |
        v
Taslak → İnceleme → Onay → Yayın
```

## Alınan veri

1. Ürün/beden: SKU, ürün adı, beden, renk, marka, tür ve statü.
2. Eş dönem işlemleri: fatura ve Credit Memo satırları, tarih, miktar, Order Type ve fulfillment location.
3. Anlık stok: SKU, lokasyon ve `quantityavailable`.

Eylül 2025 öncesi eski ürünlerle yeni SKU eşlemesi yapılmaz. Hedef dönemin tam bir yıl önceki tarih aralığı sorgulanır.

## Depo sınıflandırması

- `Zalando Fulfilled` → Zalando Offerable
- `Partner Fulfilled + Ekol location` → Ekol
- `Partner Fulfilled + Paris location` → Paris Shop

NetSuite lokasyon adlarının kanonik depo adlarıyla eşlemesi `NETSUITE_LOCATION_ALIASES` ayarında tutulur. Üç hedef depo dışında kalan stoklar hesaplamaya alınmaz ve önizlemede uyarı olarak raporlanır.

## Bağlantı ve güvenlik

- Endpoint: `POST /services/rest/query/v1/suiteql`
- Kimlik doğrulama: Token Based Authentication, OAuth 1.0 HMAC-SHA256
- Integration role: yalnızca gerekli item, transaction, transaction line ve inventory location okuma yetkileri
- Secretlar yalnızca `.env` veya sunucu secret store içinde bulunur; repoya, API yanıtına veya loga yazılmaz.
- Custom field script ID'leri konfigürasyondan alınır ve SQL identifier doğrulamasından geçirilir.
- Account-specific domain otomatik oluşturulur; sandbox `_SB1` biçimi `-sb1` domain biçimine çevrilir.

## Dayanıklılık

- SuiteQL sayfalama varsayılan 1.000 satırdır.
- HTTP 429 ve geçici 5xx yanıtlarında `Retry-After` veya exponential backoff uygulanır.
- Her istek için zaman aşımı vardır.
- Plan oluşturulmadan önce önizleme endpoint'i kaynak/kabul/ret satır sayılarını verir.
- SKU veya Order Type eksikliği nedeniyle dışlanan satış/iade satırı bloklayıcı kalite hatasıdır.
- Eksik ürün alanları ve hedef dışı stok lokasyonları açık uyarı üretir.

## Endpoint'ler

- `GET /api/v1/integrations/netsuite/status`
- `POST /api/v1/integrations/netsuite/connection-test`
- `POST /api/v1/integrations/netsuite/preview`
- `POST /api/v1/integrations/netsuite/sync-plan`

`connection-test` yalnızca yönetici rolüne açıktır. `preview` kayıt oluşturmaz. `sync-plan`, alınan veri üzerinde hesaplama ve kalite kontrollerini çalıştırarak sürümlü bir taslak plan oluşturur.

## Canlıya alma sırası

1. Sandbox read-only integration role ve TBA token oluşturulur.
2. Gerçek custom field script ID'leri `.env` içine girilir.
3. Bağlantı testi çalıştırılır.
4. Seçilen hedef dönem için önizleme alınır.
5. Kaynak ve kabul edilen satır sayıları incelenir; retler sıfırlanır veya açıklanır.
6. 10–20 SKU için NetSuite Saved Search ile brüt satış, iade, net satış ve stok mutabakatı yapılır.
7. İlk gerçek taslak plan oluşturulur ve kullanıcı kabul akışından geçirilir.
