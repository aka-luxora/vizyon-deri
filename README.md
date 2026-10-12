# Vizyon Deri Talep ve Üretim Planlama

Vizyon Deri'nin onayladığı temel yöntemle çalışan şirket içi planlama uygulaması:

1. Hedef tarih aralığının bir yıl önceki eş dönemini bulur.
2. Ürün ve beden bazında `net satış = brüt satış - iade` hesabını yapar.
3. `satış tahmini = eş dönem net satış × büyüme çarpanı` formülünü uygular.
4. `üretim ihtiyacı = max(0, tahmin - satılabilir stok)` hesabını yapar.
5. Ürün/bedenleri stok yokluğu, stok kapsaması ve açık miktarına göre önceliklendirir.
6. Üretimi Zalando Offerable, Ekol ve Paris Shop depolarına dağıtır.

Depo talebi şu kuralla sınıflandırılır:

- `Zalando Fulfilled` → Zalando Offerable
- `Partner Fulfilled + Ekol location` → Ekol
- `Partner Fulfilled + Paris location` → Paris Shop

Alternatif istatistiksel modeller, çoklu simülasyon, eski SKU eşlemesi, gelecek arz ve NetSuite'e geri yazım bu sürümün kapsamında değildir.

## Profesyonel işletim katmanı

Hesap yöntemi değiştirilmeden aşağıdaki üretim ortamı kontrolleri eklenmiştir:

- Her çalıştırmaya benzersiz plan kimliği ve aynı plan adı içinde artan sürüm numarası verilir.
- Plan girdisi, sonucu, veri anı ve kullanıcı bilgisi kalıcı SQLite veritabanında saklanır.
- Ürün tekilliği, eş dönem veri varlığı, satış/stok SKU eşleşmesi, satış mutabakatı ve depo toplamı kontrolleri otomatik çalışır.
- Bloklayıcı kalite hatası bulunan plan incelemeye, onaya veya yayına ilerleyemez.
- Ürün/beden üretim adedi yalnızca taslak aşamasında ve zorunlu gerekçeyle revize edilebilir.
- Her revizyonda eski/yeni adet, gerekçe, kullanıcı, rol ve zaman denetim izine yazılır.
- Yaşam döngüsü `Taslak → İncelemede → Onaylandı → Yayımlandı` biçimindedir.
- Planlamacı, onaycı, yayıncı, izleyici ve yönetici yetkileri API seviyesinde ayrılmıştır.
- Her veri değişikliğinden sonra geri yüklenebilir SQLite yedeği oluşturulur; son 20 yedek Docker volume içinde tutulur.
- Sağlık endpoint'i veritabanı, kayıt ve yedek durumunu bildirir.

Arayüzdeki kullanıcı/rol seçimi kabul ve süreç demosu içindir. Canlı kullanımda `x-user` ve `x-role` değerleri kullanıcı tarafından seçilmemeli; kurumun SSO/reverse-proxy katmanı tarafından güvenilir biçimde üretilmelidir.

## Çalıştırma

Docker Desktop açıkken:

```powershell
./baslat.ps1
```

Arayüz: <http://localhost:8787>

Sağlık kontrolü: <http://localhost:8787/health>

Kapatma:

```powershell
docker compose down
```

## Kabul senaryosu

Varsayılan demo, müşteri tarafından verilen örneği bire bir doğrular:

| Ölçü | Sonuç |
|---|---:|
| Eş dönem net satış | 2.000 |
| Büyüme çarpanı | 1,8 |
| Hedef satış tahmini | 3.600 |
| Satılabilir stok | 1.700 |
| Üretim ihtiyacı | 1.900 |
| Zalando Offerable | 1.100 |
| Ekol | 700 |
| Paris Shop | 100 |

## API

- `GET /health`
- `GET /api/v1/meta`
- `POST /api/v1/plans/demo` — arayüzün kullandığı kabul verisi
- `POST /api/v1/plans/portfolio` — NetSuite adaptörünün göndereceği gerçek veri sözleşmesi
- `POST /api/v1/plans/calculate` — geriye dönük uyumlu tek SKU endpoint'i
- `POST /api/v1/managed-plans/demo` — demo verisiyle kalıcı ve kontrollü plan oluşturur
- `POST /api/v1/managed-plans/portfolio` — gerçek veriyle kalıcı ve kontrollü plan oluşturur
- `GET /api/v1/managed-plans` — plan geçmişini listeler
- `GET /api/v1/managed-plans/{plan_id}` — plan, kalite kontrolleri ve denetim izini getirir
- `POST /api/v1/managed-plans/{plan_id}/override` — gerekçeli üretim revizyonu
- `POST /api/v1/managed-plans/{plan_id}/transition` — inceleme/onay/yayın geçişi
- `GET /docs` — OpenAPI sözleşmesi (backend portu açılırsa)

Gerçek entegrasyonda NetSuite adaptörü `/api/v1/plans/portfolio` sözleşmesine şu veri ailelerini sağlar:

- ürün master: SKU, model, renk, beden, marka, tür, statü;
- eş dönem satış/iade: tarih, adet, Order Type, fulfillment location;
- anlık satılabilir stok: SKU + depo + adet.

## NetSuite canlı bağlantı hazırlığı

Read-only REST SuiteQL adaptörü uygulamaya dahildir. Token Based Authentication, sayfalama, 429/5xx tekrar denemesi, güvenli field-ID doğrulaması, lokasyon eşleme, veri önizleme ve kontrollü plan oluşturma desteklenir.

1. [`.env.example`](.env.example) dosyasını `.env` adıyla kopyalayın.
2. Sandbox account ID, read-only integration tokenları ve gerçek custom field script ID'lerini girin.
3. `docker compose up --build -d` ile servisleri yeniden başlatın.
4. Önce durum ve bağlantı testini, sonra veri önizlemeyi çalıştırın.

Kimlik bilgileri API yanıtlarında veya loglarda gösterilmez. `.env` ve yerel veritabanı Git dışında tutulur. Entegrasyon yalnızca okur; NetSuite'e Demand Plan, Work Order veya Transfer Order yazmaz.

Canlı bağlantı endpoint'leri:

- `GET /api/v1/integrations/netsuite/status` — eksik ayarları ve alan haritasını gösterir
- `POST /api/v1/integrations/netsuite/connection-test` — yalnızca yönetici, küçük bir SuiteQL sorgusuyla bağlantıyı sınar
- `POST /api/v1/integrations/netsuite/preview` — veriyi çeker, dönüştürür ve kaydetmeden mutabakat özeti üretir
- `POST /api/v1/integrations/netsuite/sync-plan` — kalite kontrollü, sürümlü plan kaydı oluşturur

## Test

```powershell
cd backend
python -m pytest -q -p no:cacheprovider
```

Test paketi; 2.000 → 3.600 → 1.900 kabul hesabını, 1.100 / 700 / 100 depo toplamlarını, rol yetkilerini, gerekçeli revizyonu, denetim izini ve tüm onay yaşam döngüsünü sabitler.
