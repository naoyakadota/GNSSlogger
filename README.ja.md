# GPS Logger 2.2.0

[English](README.md) | 日本語

USB接続したGNSS受信機から`$`で始まるNMEAセンテンスを日毎テキストファイルへ記録し、記録済みファイルをFTPサーバーへ送るRaspberry Pi用サービスです。

原作者: **Naoya Kadota** — https://nkadota.com

## 1. 主な機能

- 機器・実験サイトごとに1日1個のログを作成
- USBシリアルポートとbaud rateの自動判定
- NMEA出力開始命令が必要な受信機向けのテキストstartup commands
- シリアル切断後の自動再接続
- BCM GPIO17を使ったGNSS受信機のリセット（Optional: 受信機に対する配線が別途必要です）
- 毎日のFTP転送と、失敗後1時間ごとの再試行
- systemdによる自動起動・自動再起動
- 機体設定とFTP認証情報をGit repositoryの外に保存

先頭文字が`$`である行を記録対象とします。NMEA checksumの検証は行いません。

## 2. 対応環境

システムはDebian 13（Trixie）ベースの現行Raspberry Pi OSを想定しています。

- Raspberry Pi OS LiteまたはDesktop
- 32-bitまたは64-bit
- インストール時にネットワーク接続されたRaspberry Pi
- テキストNMEAを出力できるUSBシリアルGNSS受信機

Raspberry Pi OS公式download:
https://www.raspberrypi.com/software/operating-systems/

## 3. ハードウェア

### 必須

- Raspberry Pi（Pi 3A+推奨。他のRaspberry Piでも動作する仕様ですが未検証です）
- USB接続のGNSS受信機
- Raspberry Pi用ローカルストレージ（SDカードまたはSSD）と電源
- ネットワーク接続（セットアップ時とFTPサーバーへの日次アップロード時）

### 任意のGPIOリセット

ZED-F9P（例: Ardusimple SimpleRTK2B）等、一部の受信機にはリセットピンがあります。この機能を使用する場合は、Raspberry Piの**BCM GPIO17**（物理pin 11）を受信機のactive-low `RESET_N`へ接続します。Raspberry Piと受信機のgroundは共通にしてください。
https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio

`$`で始まるセンテンスを300秒間受信しない場合、GPIO17を200 ms LowにしてからHighへ戻し、serialへ再接続します。これはbest-effort動作です。配線なし、GPIO初期化失敗、リセット失敗のいずれでもprogramは停止しません。

GPIO17を別用途で使う機体では、運用前に`config.ini`の`[reset]`で`enabled = false`へ変更してください。

u-blox ZED-F9P Integration ManualではRESET_Nはactive-low、必要なLow時間は100 ms以上です。本packageでは200 msを使用します。
https://content.u-blox.com/sites/default/files/ZED-F9P_IntegrationManual_UBX-18010802.pdf

## 4. GitHubからのインストール

```bash
sudo apt update
sudo apt install -y git python3-venv python3-lgpio
git clone https://github.com/naoyakadota/GNSSlogger
cd GNSSlogger
sudo ./setup.sh
```

`python3-lgpio`は、現行Raspberry Piでgpiozeroが利用するGPIO backendを提供します。
installerは、このsystem packageを専用virtual environmentから参照できるようにします。

installerが質問する内容:

1. device/site名
2. FTP server address
3. FTP user ID
4. FTP password（入力は画面に表示されません）
5. FTP上の親directory（device/site名は自動付加）
6. ローカルGNSS data directory

ローカル保存先の既定値:

```text
/home/<インストール実行ユーザー>/GPS_data/<device-name>/
```

baudは`auto`、GPIO resetは有効・timeout 5分、upload時刻はRaspberry Pi OSのシステム時間で00:10が既定値です。これらの設定は`config.ini`で変更可能です。

例えば、`device/site名`に`ELORA1`、FTP親directoryに`/Naoya_FieldSensors/GPS`を入力すると、実際の保存先は次のように自動生成されます。

```text
/Naoya_FieldSensors/GPS/ELORA1
```

### FTP事前検証

serviceを有効にする前に、installerは次を実行します。

1. FTP接続とlogin
2. remote pathの既存階層へ順番に移動
3. 存在しない階層だけを作成
4. 最下層のdevice/site folderが既存なら利用確認
5. 小さなrandom test fileをupload
6. remote sizeを照合
7. test fileを削除

既存の親directoryには作成権限が不要です。必要うなdirectoryを作れない場合や、upload・size確認・削除に失敗した場合、serviceを有効にせずインストールを終了します。

最下層folder（device/site名）が既存の場合、次のように確認します。

```text
Remote directory already exists: /Naoya_FieldSensors/GPS/ELORA1
Existing .log files: 127; other entries: 2
Use this existing directory for this device? [y/N]:
```

`y`を入力してEnterを押すと、その既存フォルダを使用します。異なる機器のデータを誤って混ぜないため、既定はNoです。

## 5. 配置ファイルと生成ファイル

ファイル配置は以下の通りです。

```text
Git clone（source）          ~/GNSSlogger/
実行プログラム               /opt/gps-logger/GPSlogger.py
Python仮想環境               /opt/gps-logger/.venv/
認証情報・設定情報           /etc/gps-logger/config.ini
起動時の追加serial commands  /etc/gps-logger/startup_commands.txt
systemd unit                /etc/systemd/system/gps-logger.service
GNSS観測データ               ~/GPS_data/<device-name>/
アップロード済み観測データ   ~/GPS_data/<device-name>/Uploaded/
```
インストール終了時にデータ保存先パスを表示します。

生成例:

```text
/home/pi/GPS_data/ELORA1/
├── elora1_2026-09-26.log        # 当日分・記録中
└── Uploaded/
    └── elora1_2026-09-25.log    # 転送・検証済み
```

生成されたファイルはFTPサーバーへアップロード・検証された後に`Uploaded/`へ自動で移動します。当日記録中のfileは転送しません。

## 6. ファイルが生成されているか確認する

installerの最後に、実際のパスを含む確認コマンドを表示します。表示されたコマンドを利用することで、システムの動作状態を確認できます。

以下は例です。自身のセットアップに合わせてdevice名を置き換えてください。

```bash
ls -lh /home/pi/GPS_data/<device-name>/
tail -f /home/pi/GPS_data/<device-name>/<device-name小文字>_$(date +%F).log
systemctl status gps-logger.service
journalctl -u gps-logger.service -f
```

正常時の目安:

- `systemctl status`が`active (running)`
- 当日日付のfileが存在
- `tail -f`に新しい`$...`センテンスが表示
- 受信中にfile sizeが増加

`tail`または`journalctl -f`は`Ctrl+C`で終了できます。service自体は停止しません。

## 7. 設定変更

root権限で編集します。

```bash
sudo nano /etc/gps-logger/config.ini
```

nanoでは`Ctrl+O`、Enterで保存し、`Ctrl+X`で終了します。変更を反映します。

```bash
sudo systemctl restart gps-logger.service
```

所有者はroot、権限は`0600`です。rootは読み書きできますが、一般userからFTP passwordを読めません。

### 主な設定

```ini
[device]
name = ELORA1

[ftp]
host = ftp.example.org
port = 21
user = gps-logger
password = local-secret
remote_dir = /YourFirstDirectory/YourSecondDirectory/<YourDeviceName>

[serial]
port = auto
baud = auto
auto_baud_candidates = 115200,9600,38400,57600,230400,460800
auto_probe_sec = 3.0
read_timeout_sec = 1.0
startup_commands_file = /etc/gps-logger/startup_commands.txt
startup_command_delay_sec = 0.2
startup_command_terminator = CRLF

[reset]
enabled = true
gpio_bcm = 17
after_no_valid_nmea_sec = 300
pulse_sec = 0.2
reconnect_wait_sec = 5

[upload]
daily_at = 00:10
retry_interval_minutes = 60

[storage]
log_dir = /home/pi/GPS_data/<YourDeviceName>
```

実際のpasswordをGit、README、screenshot、issue reportへ入れないでください。

## 8. Serial portとbaudの判定

新規installationでは`port = auto`を保存します。起動時とserial切断後の再接続時に、次を確認します。

1. `/dev/serial/by-id/*`
2. pySerialが報告する`/dev/ttyACM*`と`/dev/ttyUSB*`

これにより、受信機を別個体へ交換して`/dev/serial/by-id/`名が変わっても再検出できます。NMEAを出力するserial機器が複数ある場合は、使用する`/dev/serial/by-id/...` pathを`port`へ手動指定してください。

`baud = auto`では、設定された候補を順番に試し、`$`で始まるセンテンスを受信できた値を採用します。portやbaudが既知なら固定値も指定できます。

## 9. Startup serial commands

任意のcommand fileを編集します。

```bash
sudo nano /etc/gps-logger/startup_commands.txt
sudo systemctl restart gps-logger.service
```

規則:

- 1行に1個のtext serial message
- 空行は無視
- `#`で始まる行はcomment
- 既定でCRLFを追加
- serial接続・再接続のたびに送信
- command間は0.2秒待機

自動でNMEAを出力する受信機ではcommentだけの状態にしてください。UTF-8/ASCII text command用で、binary command packetには対応しません。

## 10. FTP転送動作

- 通常の初回: 毎日00:10（Raspberry Piのlocal time）
- service起動時に過去日fileがあれば即時試行
- 失敗後: 成功するまで60分ごとに再試行
- 当日file: 転送しない
- 転送名: 最初は非表示の`.partial` file
- 検証: localとremoteのbyte sizeが一致すること
- 公開: 検証済みpartialを最終`.log`名へrename
- local archive: 検証後のみ`Uploaded/`へ移動

remoteに同名・同sizeの最終fileがある場合は既に転送済みとみなし、local fileをarchiveします。sizeが異なる場合は競合としてlocal fileを保持し、errorを記録します。

FTP失敗でGNSS記録やuploader threadが停止することはありません。

## 11. Service操作

```bash
sudo systemctl start gps-logger.service
sudo systemctl stop gps-logger.service
sudo systemctl restart gps-logger.service
systemctl status gps-logger.service
journalctl -u gps-logger.service -f
```

root-only設定と任意GPIOへアクセスするため、serviceはrootで実行します。`Restart=always`で、終了後10秒待って再起動します。

## 12. Update

最初にcloneしたdirectoryで実行します。

```bash
cd ~/GNSSlogger
git pull
sudo ./setup.sh
```

既存値は再提示または保持されます。startup command fileは上書きしません。既存の最下層FTP directory確認を含む試験送信を再実行します。

## 13. GitHub以前・パッケージ化していないバージョンからの移行

旧serviceの自動検出・削除は意図的に実装していません。2.2.0を入れる前に候補を一覧し、旧serviceを手動停止してください。

```bash
systemctl list-unit-files | grep -i gps
sudo systemctl disable --now <old-service-name>
```

旧・新loggerを同時起動しないでください。同じserial portの競合や重複記録が起きます。

installerは古いlogを自動削除・移動しません。以前の保存先を継続する場合はsetup中にそのpathを指定するか、旧service停止後に手動移動してください。

## 14. Troubleshooting

### Serviceが再起動を繰り返す

```bash
systemctl status gps-logger.service
journalctl -u gps-logger.service -n 100 --no-pager
```

まず設定構文とfile pathを確認してください。

### Serial deviceが見つからない

```bash
ls -l /dev/serial/by-id/ 2>/dev/null
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

USB再接続、受信機電源、journalを確認します。必要なら`port`へ正しい`/dev/serial/by-id/...` pathを指定します。

### Serial接続済みだがlogができない

- 受信機が`$`で始まるtext NMEAを出しているか確認
- journalのbaud判定messageを確認
- 必要なtext commandを`startup_commands.txt`へ追加
- 受信機がbinary-only出力になっていないか確認

### GPIO warningを繰り返す

リセット線が未接続または断線している可能性があります。が、この警告が出てもプログラムはUSBからのデータ取得を継続します (受信機側になんらかの問題が発生している場合はこの限りではありません．) 。GPIO17を他用途で使う場合は設定ファイル`config.ini`内、`[reset]`の`enabled = false`へ変更してserviceを再起動することで受信機リセット機能を無効化できます。

### FTPに失敗する

journalでlogin、path、権限、size確認、同名file競合のerrorを確認してください。完全な`remote_dir`が正しいか確認します。未検証local fileは保持され、1時間ごとに再試行します。

## 15. セキュリティ

- FTPは認証情報とdataを暗号化しませんs。適切に管理されたnetworkでのみ使用してください。SFTPは未実装です。
- `config.ini`には復元可能なFTP passwordがあるため、権限`0600`を維持します。　そのため、rootのパスワード管理には注意してください。
- serviceはrootで実行されます

更新履歴は[CHANGELOG.md](CHANGELOG.md)を参照してください。
