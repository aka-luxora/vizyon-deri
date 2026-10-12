# Vizyon Deri Planlama Uygulaması

## NetSuite Bilgi ve Erişim İhtiyaçları

**Dokümanın amacı:** Vizyon Deri talep ve üretim planlama uygulamasının demo veri setinden gerçek NetSuite verisine geçirilmesi için gerekli alanları, erişimleri ve doğrulama adımlarını netleştirmek.

## 1. Önemli açıklama

Mevcut demo ekranında görülen aşağıdaki değerler, Vizyon Deri'nin tam ana veri listesi değildir:

| Alan | Demoda görülen değerler | Açıklama |
|---|---|---|
| Ürün Türü | Kürk, Deri | Müşteri tarafından paylaşılan örnek senaryodan alınmıştır. |
| Statü | S1 | Tam statü listesi henüz paylaşılmamıştır. |
| Marka | VSP | Tam marka listesi henüz paylaşılmamıştır. |

Bu değerler yalnızca kabul senaryosunu çalıştırmak amacıyla kullanılmıştır. Vizyon Deri'de yalnızca bu değerlerin bulunduğu varsayılmamaktadır.

Canlı kullanımda **Tür, Statü ve Marka seçenekleri NetSuite ürün ana verisinden dinamik olarak alınacaktır.** Yeni bir değer NetSuite'e eklendiğinde uygulamanın kod değişikliği gerektirmeden bu değeri filtrelerde gösterebilmesi hedeflenmektedir.

## 2. Onaylanan planlama yöntemi

Uygulamada kullanılacak temel yöntem:

```text
Net satış = Brüt satış - İade

Satış tahmini = Geçen yılın aynı dönemindeki net satış × Büyüme çarpanı

Üretim ihtiyacı = max(0, Satış tahmini - Mevcut satılabilir stok)
```

Hesaplama ürün ve beden seviyesinde yapılacaktır. Bulunan üretim ihtiyacı aşağıdaki depolara dağıtılacaktır:

- Zalando Offerable
- Ekol
- Paris Shop

Bu aşamada doğrusal regresyon, hareketli ortalama, mevsimsel ortalama, çoklu simülasyon, eski-yeni SKU eşlemesi, gelecek arz ve NetSuite'e üretim emri yazılması kapsamda değildir.

## 3. NetSuite ürün ana verisi

NetSuite ekibinden aşağıdaki alanların gerçek **script ID** bilgilerinin paylaşılması beklenmektedir:

| Gerekli bilgi | Örnek değer | NetSuite script ID | Zorunlu |
|---|---|---|---:|
| SKU / ürün kodu | KK61450-BROWN-36 | Standart `itemid` | Evet |
| Ürün/model adı | Lyza Shearling Coat | Standart veya custom alan | Evet |
| Beden | 36 | Paylaşılacak | Evet |
| Renk | Clotho Brown | Paylaşılacak | Hayır |
| Marka | VSP | Paylaşılacak | Evet |
| Ürün türü | Kürk | Paylaşılacak | Evet |
| Ürün statüsü | S1 | Paylaşılacak | Evet |
| Aktif/pasif bilgisi | Aktif | Standart `isinactive` | Evet |

### Beklenen karar

Tür, Statü ve Marka alanları için aşağıdaki konular doğrulanmalıdır:

1. Alanlar item kartında mı, matrix parent üzerinde mi tutuluyor?
2. Alan tipi liste, metin veya custom segment mi?
3. Beden ve renk bilgisi item alanında mı, matrix option üzerinden mi geliyor?
4. Pasif ürünler planlama ekranında tamamen hariç mi tutulacak?
5. Filtrelerde gösterilecek değerler için ayrıca bir aktif/pasif tanımı var mı?

## 4. Satış ve iade verisi

Planlama için para tutarı değil, adet bilgisi kullanılacaktır.

Her satış/iade satırında aşağıdaki bilgiler gereklidir:

| Alan | Kullanım amacı | Zorunlu |
|---|---|---:|
| İşlem tarihi | Eş dönem seçimi | Evet |
| İşlem türü | Satış veya iade ayrımı | Evet |
| SKU / item internal ID | Ürün ve beden eşlemesi | Evet |
| Miktar | Brüt satış ve iade adedi | Evet |
| Order Type | Zalando Fulfilled / Partner Fulfilled ayrımı | Evet |
| Fulfillment location | Ekol / Paris ayrımı | Partner Fulfilled için evet |
| İşlem ID ve satır unique key | Tekilleştirme ve denetim izi | Evet |

### NetSuite ekibinden beklenen doğrulamalar

- Satış için esas alınacak kayıt türü `Invoice` mıdır?
- İadeler `Credit Memo` üzerinden mi alınmalıdır?
- İade satırında Order Type bilgisi doğrudan bulunuyor mu?
- Bulunmuyorsa `createdfrom` ilişkisiyle kaynak siparişten alınabilir mi?
- İptal edilmiş veya void edilmiş kayıtlar nasıl ayırt edilmektedir?
- Miktarlar stok birimiyle aynı mıdır?
- Aynı satışın Invoice ve Item Fulfillment üzerinden iki kez sayılma riski var mıdır?

## 5. Order Type ve depo yönlendirme kuralı

Onaylanan sınıflandırma aşağıdaki gibidir:

| Order Type | Siparişin karşılandığı lokasyon | Planlama deposu |
|---|---|---|
| Zalando Fulfilled | Fark etmez | Zalando Offerable |
| Partner Fulfilled | Ekol lokasyonu | Ekol |
| Partner Fulfilled | Paris lokasyonu | Paris Shop |

NetSuite ekibinden aşağıdaki bilgiler beklenmektedir:

- Order Type alanının gerçek script ID'si
- Alanda kullanılan gerçek değerlerin yazım biçimi
- Alanın transaction header veya transaction line üzerinde bulunup bulunmadığı
- Ekol lokasyonunun NetSuite internal ID ve tam adı
- Paris lokasyonunun NetSuite internal ID ve tam adı
- Zalando Offerable lokasyonunun NetSuite internal ID ve tam adı
- Aynı depoyu temsil eden birden fazla NetSuite lokasyonu olup olmadığı

Order Type veya gerekli lokasyon bilgisi boş olan satış satırları sessizce dağıtılmayacaktır. Bu satırlar veri kalite hatası olarak raporlanacak ve planın onay sürecine ilerlemesi engellenecektir.

## 6. Satılabilir stok

Üretim ihtiyacında kullanılacak stok, muhasebesel toplam stok değil **satılabilir stok** olmalıdır.

NetSuite ekibinden aşağıdaki konuların doğrulanması beklenmektedir:

1. Satılabilir stok için `quantityavailable` alanı kullanılabilir mi?
2. Bu miktar committed siparişleri zaten düşüyor mu?
3. Bloke, kalite kontrol, hasarlı veya satışa kapalı stok ayrıca tutuluyor mu?
4. Inventory Status özelliği kullanılıyor mu?
5. Kanal için rezerve edilmiş stok var mı?
6. Negatif stok değerleri mümkün mü?
7. Ekol, Paris Shop ve Zalando Offerable dışında planlamaya dahil edilmesi gereken başka lokasyon var mı?

İlk kabul yaklaşımı:

```text
Satılabilir stok = NetSuite tarafından doğrulanan offerable/available miktar
```

Satılabilir stok formülü NetSuite ekibi ve iş birimi tarafından yazılı olarak onaylanmadan canlı üretim kararı verilmemelidir.

## 7. Eylül 2025 veri sınırı

Eski ve yeni SKU'ların birleştirilmesi istenmemektedir. Analiz yeni ürün yapısına ait **Eylül 2025 ve sonrası** veriyi esas alacaktır.

Netleştirilmesi gereken konu:

- Eylül 2025 başlangıcı için yalnızca işlem tarihi yeterli midir?
- Yeni ürün yapısını ayrıca belirleyen sezon, oluşturulma tarihi, marka veya başka bir alan var mıdır?

Eski SKU'lar ile yeni SKU'lar arasında otomatik performans aktarımı yapılmayacaktır.

## 8. Read-only NetSuite erişimi

Uygulamanın canlı bağlantısı için ayrı bir read-only integration role önerilmektedir.

Gerekli erişimler:

- Item ve ilgili custom alanları okuma
- Transaction ve transaction line okuma
- Invoice ve Credit Memo okuma
- Location okuma
- Inventory item location / satılabilir stok okuma
- REST Web Services ve SuiteAnalytics Workbook/SuiteQL kullanımı

Gerekli teknik bilgiler:

| Bilgi | Açıklama |
|---|---|
| Account ID | Öncelikle sandbox hesabı |
| Consumer Key | Integration Record üzerinden |
| Consumer Secret | Güvenli kanaldan paylaşılmalı |
| Token ID | Read-only kullanıcı/rol için |
| Token Secret | Güvenli kanaldan paylaşılmalı |

Consumer Secret ve Token Secret e-posta, Teams mesajı veya doküman içinde açık olarak paylaşılmamalıdır. Kurumun onayladığı parola kasası veya güvenli secret aktarım yöntemi kullanılmalıdır.

## 9. İlk mutabakat çalışması

Canlı bağlantı kurulduktan sonra doğrudan üretim kararı alınmamalıdır. Önce 10–20 SKU içeren kontrollü bir örneklemle aşağıdaki karşılaştırma yapılmalıdır:

| Kontrol | NetSuite sonucu | Uygulama sonucu | Beklenti |
|---|---:|---:|---|
| Brüt satış |  |  | Bire bir eşit |
| İade |  |  | Bire bir eşit |
| Net satış |  |  | Bire bir eşit |
| Satılabilir stok |  |  | Bire bir eşit |
| Büyüme çarpanı |  |  | Seçilen değerle eşit |
| Satış tahmini |  |  | Formülle eşit |
| Üretim ihtiyacı |  |  | Formülle eşit |
| Zalando alokasyonu |  |  | İşlem satırına kadar açıklanabilir |
| Ekol alokasyonu |  |  | İşlem satırına kadar açıklanabilir |
| Paris alokasyonu |  |  | İşlem satırına kadar açıklanabilir |

Fark bulunan kayıtlar toplam seviyesinde kapatılmamalı; transaction ID ve satır unique key seviyesinde incelenmelidir.

## 10. İş biriminden beklenen kararlar

Aşağıdaki istisna kuralları canlı öncesinde yazılı olarak karara bağlanmalıdır:

- Geçmiş net satışı sıfır olan yeni ürün nasıl ele alınacak?
- İadesi satışından yüksek olan ürün için tahmin sıfır mı kabul edilecek?
- Satılabilir stok tahminden yüksekse üretim ihtiyacı sıfır mı olacak?
- Order Type boşsa kayıt bloklanacak mı?
- Partner Fulfilled olup fulfillment location boşsa kayıt bloklanacak mı?
- NetSuite'te negatif available stok varsa sıfır mı kabul edilecek, yoksa hata mı üretilecek?
- Plan onaylandıktan sonra NetSuite verisi değişirse yeni plan sürümü mü oluşturulacak?

Mevcut güvenli varsayım, eksik Order Type veya lokasyon bilgisinin veri kalite hatası sayılması ve ilgili planın onaya ilerletilmemesidir.

## 11. Ekipten istenen teslim paketi

NetSuite/ERP ekibinden aşağıdaki paketin hazırlanması rica edilmektedir:

- [ ] Sandbox read-only entegrasyon erişimi
- [ ] Account ID
- [ ] Güvenli kanaldan TBA token bilgileri
- [ ] Beden field script ID'si
- [ ] Renk field script ID'si
- [ ] Marka field script ID'si
- [ ] Ürün Türü field script ID'si
- [ ] Statü field script ID'si
- [ ] Order Type field script ID'si
- [ ] Üç hedef deponun internal ID ve tam adları
- [ ] Satılabilir stok formülünün açıklaması
- [ ] Satış ve iade için kontrol Saved Search çıktısı
- [ ] Mutabakat için seçilen 10–20 SKU listesi
- [ ] İstisna kurallarına ilişkin iş birimi kararları

## 12. Sonraki adımlar

Bilgiler tamamlandığında çalışma sırası şöyledir:

1. Sandbox bağlantı testi
2. Ürün, satış/iade ve stok veri önizlemesi
3. Tür, Statü ve Marka filtrelerinin NetSuite ana verisinden dinamik yüklenmesi
4. Dışlanan veya eşleşmeyen kayıtların temizlenmesi
5. 10–20 SKU mutabakatı
6. İlk gerçek taslak plan
7. Planlamacı, onaycı ve yayıncı kullanıcı kabul testi
8. Canlı sunucu kurulumu ve izleme

---

**Özet:** Ekrandaki Kürk, Deri, S1 ve VSP değerleri tam ürün ana verisi değildir. Bunlar müşteri örneğinden oluşturulan demo değerleridir. Canlı çözümde Tür, Statü ve Marka seçenekleri NetSuite'ten dinamik olarak alınacaktır.
