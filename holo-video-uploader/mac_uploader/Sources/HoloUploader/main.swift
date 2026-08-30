import AppKit
import CryptoKit
import Darwin
import Foundation

private let supportedExtensions: Set<String> = [
    "mp4", "mov", "m4v", "avi", "mkv", "webm"
]

private enum VideoLayout {
    case quad
    case single

    var fileSuffix: String { self == .quad ? "-quad" : "-single" }
}

private enum HoloError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let text): return text
        }
    }
}

private struct BackendEnvelope: Decodable {
    let id: String
    let name: String?
    let downloadURL: String?
    let mp4URL: String?
    let url: String?
    let ackURL: String?

    enum CodingKeys: String, CodingKey {
        case id, name, url
        case downloadURL = "download_url"
        case mp4URL = "mp4_url"
        case ackURL = "ack_url"
    }
}

private struct BackendVideo {
    let id: String
    let name: String
    let data: Data
    let ackURL: URL?
}

private final class BackendClient {
    private let maximumDownloadSize = 256 * 1024 * 1024

    private func request(_ request: URLRequest, timeout: TimeInterval = 90) throws -> (Data, HTTPURLResponse) {
        let semaphore = DispatchSemaphore(value: 0)
        var resultData: Data?
        var resultResponse: HTTPURLResponse?
        var resultError: Error?
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        configuration.timeoutIntervalForResource = timeout
        let session = URLSession(configuration: configuration)
        let task = session.dataTask(with: request) { data, response, error in
            resultData = data
            resultResponse = response as? HTTPURLResponse
            resultError = error
            semaphore.signal()
        }
        task.resume()
        guard semaphore.wait(timeout: .now() + timeout + 5) == .success else {
            task.cancel()
            throw HoloError.message("后端请求超时")
        }
        if let resultError { throw resultError }
        guard let response = resultResponse else {
            throw HoloError.message("后端没有返回 HTTP 响应")
        }
        let data = resultData ?? Data()
        guard data.count <= maximumDownloadSize else {
            throw HoloError.message("后端视频超过 256 MB")
        }
        return (data, response)
    }

    private func authorizedRequest(url: URL, token: String) -> URLRequest {
        var request = URLRequest(url: url)
        request.setValue("application/json, video/mp4", forHTTPHeaderField: "Accept")
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func validateMP4(_ data: Data) throws {
        guard data.count >= 12 else { throw HoloError.message("后端返回的视频为空") }
        let header = data.prefix(32)
        guard header.range(of: Data("ftyp".utf8)) != nil else {
            throw HoloError.message("后端返回的内容不是 MP4")
        }
    }

    func fetch(endpoint: URL, token: String, skippingID: String?) throws -> BackendVideo? {
        var metadataRequest = authorizedRequest(url: endpoint, token: token)
        if let skippingID, skippingID.hasPrefix("\"") {
            metadataRequest.setValue(skippingID, forHTTPHeaderField: "If-None-Match")
        }
        let (body, response) = try request(metadataRequest)
        if response.statusCode == 204 || response.statusCode == 304 { return nil }
        guard (200...299).contains(response.statusCode) else {
            throw HoloError.message("后端返回 HTTP \(response.statusCode)")
        }

        let contentType = response.value(forHTTPHeaderField: "Content-Type")?.lowercased() ?? ""
        if contentType.contains("application/json") {
            let envelope = try JSONDecoder().decode(BackendEnvelope.self, from: body)
            if envelope.id == skippingID { return nil }
            let address = envelope.downloadURL ?? envelope.mp4URL ?? envelope.url
            guard !envelope.id.isEmpty, let address, let downloadURL = URL(string: address) else {
                throw HoloError.message("后端 JSON 缺少 id 或 download_url")
            }
            let downloadToken = downloadURL.host == endpoint.host ? token : ""
            let (videoData, videoResponse) = try request(
                authorizedRequest(url: downloadURL, token: downloadToken), timeout: 180)
            guard (200...299).contains(videoResponse.statusCode) else {
                throw HoloError.message("MP4 下载返回 HTTP \(videoResponse.statusCode)")
            }
            try validateMP4(videoData)
            return BackendVideo(
                id: envelope.id,
                name: envelope.name ?? "backend-\(envelope.id).mp4",
                data: videoData,
                ackURL: envelope.ackURL.flatMap(URL.init(string:))
            )
        }

        try validateMP4(body)
        let digest = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
        let identity = response.value(forHTTPHeaderField: "ETag") ?? digest
        if identity == skippingID { return nil }
        let disposition = response.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        let suggestedName = disposition
            .components(separatedBy: "filename=").last?
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"' "))
        return BackendVideo(
            id: identity,
            name: suggestedName?.isEmpty == false ? suggestedName! : "backend-video.mp4",
            data: body,
            ackURL: nil
        )
    }

    func acknowledge(url: URL, id: String, token: String) throws {
        var request = authorizedRequest(url: url, token: token)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["id": id, "status": "played"])
        let (_, response) = try self.request(request, timeout: 20)
        guard (200...299).contains(response.statusCode) else {
            throw HoloError.message("确认接口返回 HTTP \(response.statusCode)")
        }
    }
}

private final class SerialUploader {
    private let baud = speed_t(B115200)

    private func devicePath() throws -> String {
        let names = try FileManager.default.contentsOfDirectory(atPath: "/dev")
        guard let name = names.sorted().first(where: { $0.hasPrefix("cu.usbmodem") }) else {
            throw HoloError.message("没有找到 ESP32 USB 设备")
        }
        return "/dev/\(name)"
    }

    private func writeAll(_ fd: Int32, bytes: UnsafeRawBufferPointer) throws {
        var offset = 0
        while offset < bytes.count {
            let count = Darwin.write(fd, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
            if count > 0 {
                offset += count
            } else if errno == EAGAIN || errno == EINTR {
                usleep(1_000)
            } else {
                throw HoloError.message("USB 写入失败")
            }
        }
    }

    private func send(_ fd: Int32, data: Data) throws {
        try data.withUnsafeBytes { try writeAll(fd, bytes: $0) }
    }

    private func waitFor(_ marker: String, fd: Int32, timeout: TimeInterval) throws {
        let deadline = Date().addingTimeInterval(timeout)
        var received = ""
        while Date() < deadline {
            var descriptor = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            let result = Darwin.poll(&descriptor, 1, 200)
            if result > 0, descriptor.revents & Int16(POLLIN) != 0 {
                var bytes = [UInt8](repeating: 0, count: 512)
                let count = bytes.withUnsafeMutableBytes {
                    Darwin.read(fd, $0.baseAddress, $0.count)
                }
                if count > 0 {
                    received += String(decoding: bytes.prefix(count), as: UTF8.self)
                    if received.contains(marker) { return }
                    if received.count > 8_192 { received.removeFirst(received.count - 4_096) }
                }
            }
        }
        throw HoloError.message("等待设备响应超时：\(marker)")
    }

    func upload(file: URL, progress: @escaping (Double) -> Void) throws {
        let path = try devicePath()
        let fd = Darwin.open(path, O_RDWR | O_NOCTTY | O_NONBLOCK)
        guard fd >= 0 else { throw HoloError.message("无法打开 \(path)") }
        defer { Darwin.close(fd) }

        var options = termios()
        guard tcgetattr(fd, &options) == 0 else {
            throw HoloError.message("无法配置 USB 串口")
        }
        cfmakeraw(&options)
        cfsetspeed(&options, baud)
        options.c_cflag |= tcflag_t(CLOCAL | CREAD)
        guard tcsetattr(fd, TCSANOW, &options) == 0 else {
            throw HoloError.message("无法设置 USB 速度")
        }
        tcflush(fd, TCIFLUSH)

        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        guard let total = attributes[.size] as? NSNumber else {
            throw HoloError.message("无法读取视频大小")
        }
        let totalBytes = total.intValue
        try send(fd, data: Data("UPLOAD2 \(totalBytes)\n".utf8))
        try waitFor("[USB] ready2", fd: fd, timeout: 10)

        let input = try FileHandle(forReadingFrom: file)
        defer { try? input.close() }
        var sent = 0
        while let chunk = try input.read(upToCount: 128), !chunk.isEmpty {
            try send(fd, data: chunk)
            sent += chunk.count
            try waitFor("[USB] ack \(sent)", fd: fd, timeout: 10)
            progress(Double(sent) / Double(totalBytes))
        }
        try waitFor("[USB] upload ok", fd: fd, timeout: 30)
        progress(1)
    }
}

private final class DropView: NSView {
    var onDrop: (([URL]) -> Void)?
    private let title = NSTextField(labelWithString: "把视频拖到这里")
    private let subtitle = NSTextField(labelWithString: "自动转换并通过 USB 播放")

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.borderWidth = 2
        layer?.borderColor = NSColor.systemBlue.withAlphaComponent(0.45).cgColor
        layer?.backgroundColor = NSColor.systemBlue.withAlphaComponent(0.06).cgColor
        registerForDraggedTypes([.fileURL])

        title.font = .systemFont(ofSize: 22, weight: .semibold)
        subtitle.textColor = .secondaryLabelColor
        let stack = NSStackView(views: [title, subtitle])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 7
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor)
        ])
    }

    required init?(coder: NSCoder) { nil }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        layer?.borderColor = NSColor.systemBlue.cgColor
        return .copy
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        layer?.borderColor = NSColor.systemBlue.withAlphaComponent(0.45).cgColor
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        layer?.borderColor = NSColor.systemBlue.withAlphaComponent(0.45).cgColor
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        let urls = (sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: options) as? [URL]) ?? []
        let videos = urls.filter { supportedExtensions.contains($0.pathExtension.lowercased()) }
        if !videos.isEmpty { onDrop?(videos) }
        return !videos.isEmpty
    }
}

private final class MainController: NSObject, NSTableViewDataSource, NSTableViewDelegate {
    let view = NSView()
    private let table = NSTableView()
    private let status = NSTextField(labelWithString: "连接设备后，把视频拖入窗口")
    private let progress = NSProgressIndicator()
    private let playButton = NSButton(title: "播放到设备", target: nil, action: nil)
    private let layoutPopup = NSPopUpButton()
    private let backendButton = NSButton(title: "从后端接收", target: nil, action: nil)
    private let endpointField = NSTextField()
    private let tokenField = NSSecureTextField()
    private let autoReceive = NSButton(checkboxWithTitle: "自动接收（每 10 秒）", target: nil, action: nil)
    private var videos: [URL] = []
    private var isBusy = false
    private var backendTimer: Timer?

    private lazy var libraryURL: URL = {
        let movies = FileManager.default.urls(for: .moviesDirectory, in: .userDomainMask)[0]
        return movies.appendingPathComponent("Holo Player", isDirectory: true)
    }()

    override init() {
        super.init()
        buildInterface()
        try? FileManager.default.createDirectory(at: libraryURL, withIntermediateDirectories: true)
        reloadLibrary()
        layoutPopup.selectItem(at: UserDefaults.standard.integer(forKey: "videoLayout"))
        endpointField.stringValue = UserDefaults.standard.string(forKey: "backendEndpoint")
            ?? "https://genpichong.dpdns.org/api/device/next"
        tokenField.stringValue = ProcessInfo.processInfo.environment["HOLO_DEVICE_TOKEN"] ?? ""
        let autoReceiveFromEnvironment = ProcessInfo.processInfo.environment["HOLO_AUTO_RECEIVE"] == "1"
        autoReceive.state = (
            autoReceiveFromEnvironment || UserDefaults.standard.bool(forKey: "backendAutoReceive")
        ) ? .on : .off
        backendTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            guard let self, self.autoReceive.state == .on else { return }
            self.receiveFromBackend(silent: true)
        }
    }

    private func buildInterface() {
        let dropView = DropView()
        dropView.translatesAutoresizingMaskIntoConstraints = false
        dropView.onDrop = { [weak self] urls in self?.importVideos(urls) }

        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("video"))
        column.title = "已导入视频"
        table.addTableColumn(column)
        table.headerView = nil
        table.rowHeight = 34
        table.delegate = self
        table.dataSource = self
        table.allowsEmptySelection = true
        table.doubleAction = #selector(playSelected)
        table.target = self

        let scroll = NSScrollView()
        scroll.documentView = table
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.translatesAutoresizingMaskIntoConstraints = false

        let importButton = NSButton(title: "选择视频…", target: self, action: #selector(chooseVideos))
        playButton.target = self
        playButton.action = #selector(playSelected)
        playButton.keyEquivalent = "\r"

        layoutPopup.addItems(withTitles: ["四面全息（头朝外）", "单画面全屏"])
        layoutPopup.target = self
        layoutPopup.action = #selector(layoutChanged)

        endpointField.placeholderString = "https://backend.example.com/api/device/next"
        tokenField.placeholderString = "Bearer Token（可选，本次运行有效）"
        backendButton.target = self
        backendButton.action = #selector(receiveFromBackendButton)
        autoReceive.target = self
        autoReceive.action = #selector(autoReceiveChanged)

        let endpointLabel = NSTextField(labelWithString: "接口")
        endpointLabel.alignment = .right
        let tokenLabel = NSTextField(labelWithString: "Token")
        tokenLabel.alignment = .right
        let backendGrid = NSGridView(views: [
            [endpointLabel, endpointField, backendButton],
            [tokenLabel, tokenField, autoReceive]
        ])
        backendGrid.rowSpacing = 8
        backendGrid.columnSpacing = 8
        backendGrid.column(at: 0).xPlacement = .trailing
        backendGrid.column(at: 1).width = 370
        backendGrid.translatesAutoresizingMaskIntoConstraints = false

        let backendBox = NSBox()
        backendBox.title = "后端 MP4 接收"
        backendBox.boxType = .primary
        backendBox.translatesAutoresizingMaskIntoConstraints = false
        backendBox.contentView?.addSubview(backendGrid)
        if let content = backendBox.contentView {
            NSLayoutConstraint.activate([
                backendGrid.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 10),
                backendGrid.trailingAnchor.constraint(lessThanOrEqualTo: content.trailingAnchor, constant: -10),
                backendGrid.centerYAnchor.constraint(equalTo: content.centerYAnchor)
            ])
        }

        progress.isIndeterminate = false
        progress.minValue = 0
        progress.maxValue = 1
        progress.doubleValue = 0
        progress.translatesAutoresizingMaskIntoConstraints = false

        status.lineBreakMode = .byTruncatingMiddle
        status.textColor = .secondaryLabelColor
        status.translatesAutoresizingMaskIntoConstraints = false

        let layoutLabel = NSTextField(labelWithString: "新视频布局：")
        let buttons = NSStackView(views: [importButton, playButton, layoutLabel, layoutPopup])
        buttons.spacing = 10
        buttons.translatesAutoresizingMaskIntoConstraints = false

        [dropView, backendBox, scroll, buttons, progress, status].forEach { view.addSubview($0) }
        NSLayoutConstraint.activate([
            dropView.topAnchor.constraint(equalTo: view.topAnchor, constant: 22),
            dropView.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 22),
            dropView.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -22),
            dropView.heightAnchor.constraint(equalToConstant: 130),

            backendBox.topAnchor.constraint(equalTo: dropView.bottomAnchor, constant: 14),
            backendBox.leadingAnchor.constraint(equalTo: dropView.leadingAnchor),
            backendBox.trailingAnchor.constraint(equalTo: dropView.trailingAnchor),
            backendBox.heightAnchor.constraint(equalToConstant: 96),

            scroll.topAnchor.constraint(equalTo: backendBox.bottomAnchor, constant: 14),
            scroll.leadingAnchor.constraint(equalTo: dropView.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: dropView.trailingAnchor),
            scroll.bottomAnchor.constraint(equalTo: buttons.topAnchor, constant: -16),

            buttons.leadingAnchor.constraint(equalTo: dropView.leadingAnchor),
            buttons.bottomAnchor.constraint(equalTo: progress.topAnchor, constant: -12),

            progress.leadingAnchor.constraint(equalTo: dropView.leadingAnchor),
            progress.trailingAnchor.constraint(equalTo: dropView.trailingAnchor),
            progress.bottomAnchor.constraint(equalTo: status.topAnchor, constant: -8),

            status.leadingAnchor.constraint(equalTo: dropView.leadingAnchor),
            status.trailingAnchor.constraint(equalTo: dropView.trailingAnchor),
            status.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -18)
        ])
    }

    func numberOfRows(in tableView: NSTableView) -> Int { videos.count }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let identifier = NSUserInterfaceItemIdentifier("cell")
        let cell = (tableView.makeView(withIdentifier: identifier, owner: self) as? NSTableCellView) ?? NSTableCellView()
        if cell.textField == nil {
            let label = NSTextField(labelWithString: "")
            label.translatesAutoresizingMaskIntoConstraints = false
            cell.addSubview(label)
            cell.textField = label
            NSLayoutConstraint.activate([
                label.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 8),
                label.centerYAnchor.constraint(equalTo: cell.centerYAnchor),
                label.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -8)
            ])
            cell.identifier = identifier
        }
        cell.textField?.stringValue = videos[row].deletingPathExtension().lastPathComponent
        return cell
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        playButton.isEnabled = table.selectedRow >= 0 && !isBusy
    }

    private func reloadLibrary(selecting selected: URL? = nil) {
        videos = ((try? FileManager.default.contentsOfDirectory(
            at: libraryURL,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        )) ?? []).filter { $0.pathExtension.lowercased() == "avi" }.sorted {
            $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending
        }
        table.reloadData()
        if let selected, let row = videos.firstIndex(of: selected) {
            table.selectRowIndexes(IndexSet(integer: row), byExtendingSelection: false)
            table.scrollRowToVisible(row)
        }
        playButton.isEnabled = table.selectedRow >= 0 && !isBusy
    }

    @objc private func chooseVideos() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.movie]
        if panel.runModal() == .OK { importVideos(panel.urls) }
    }

    @objc private func layoutChanged() {
        UserDefaults.standard.set(layoutPopup.indexOfSelectedItem, forKey: "videoLayout")
    }

    private var selectedLayout: VideoLayout {
        layoutPopup.indexOfSelectedItem == 0 ? .quad : .single
    }

    private func safeName(for url: URL) -> String {
        let base = url.deletingPathExtension().lastPathComponent
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_ "))
        let cleaned = base.unicodeScalars.map { allowed.contains($0) ? Character(String($0)) : "_" }
        let name = String(cleaned).trimmingCharacters(in: .whitespaces)
        return name.isEmpty ? "video" : name
    }

    private func importVideos(_ urls: [URL]) {
        guard !isBusy else { return }
        let accepted = urls.filter { supportedExtensions.contains($0.pathExtension.lowercased()) }
        guard !accepted.isEmpty else {
            status.stringValue = "请选择 MP4、MOV、AVI、MKV 或 WebM 视频"
            return
        }
        let layout = selectedLayout
        setBusy(true, text: "准备转换…")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                var lastOutput: URL?
                for (index, input) in accepted.enumerated() {
                    let output = self.libraryURL.appendingPathComponent(
                        "\(self.safeName(for: input))\(layout.fileSuffix).avi")
                    self.updateStatus("转换 \(index + 1)/\(accepted.count)：\(input.lastPathComponent)", progress: 0)
                    try self.convert(input: input, output: output, layout: layout)
                    self.updateStatus("USB 上传：\(input.lastPathComponent)", progress: 0)
                    try SerialUploader().upload(file: output) { value in
                        self.updateStatus("USB 上传：\(Int(value * 100))%", progress: value)
                    }
                    lastOutput = output
                }
                DispatchQueue.main.async {
                    self.reloadLibrary(selecting: lastOutput)
                    self.setBusy(false, text: "上传完成，设备正在循环播放")
                }
            } catch {
                DispatchQueue.main.async {
                    self.reloadLibrary()
                    self.setBusy(false, text: "失败：\(error.localizedDescription)")
                }
            }
        }
    }

    @objc private func receiveFromBackendButton() {
        receiveFromBackend(silent: false)
    }

    @objc private func autoReceiveChanged() {
        UserDefaults.standard.set(autoReceive.state == .on, forKey: "backendAutoReceive")
        if autoReceive.state == .on { receiveFromBackend(silent: false) }
    }

    private func receiveFromBackend(silent: Bool) {
        guard !isBusy else { return }
        let address = endpointField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if address.isEmpty {
            if !silent { status.stringValue = "请先填写后端接口 URL" }
            return
        }
        guard let endpoint = URL(string: address),
              endpoint.scheme == "https" ||
                (endpoint.scheme == "http" && ["localhost", "127.0.0.1"].contains(endpoint.host ?? "")) else {
            status.stringValue = "接口必须使用 HTTPS（本机测试可使用 localhost）"
            return
        }

        let token = tokenField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let layout = selectedLayout
        UserDefaults.standard.set(address, forKey: "backendEndpoint")
        setBusy(true, text: "正在检查后端 MP4…")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                let client = BackendClient()
                let previousReceipt = UserDefaults.standard.string(forKey: "backendLastReceipt")
                let receiptPrefix = "\(endpoint.absoluteString)|"
                let previousID = previousReceipt?.hasPrefix(receiptPrefix) == true
                    ? String(previousReceipt!.dropFirst(receiptPrefix.count)) : nil
                guard let item = try client.fetch(endpoint: endpoint, token: token, skippingID: previousID) else {
                    DispatchQueue.main.async { self.setBusy(false, text: "后端暂无新视频") }
                    return
                }

                let receipt = "\(endpoint.absoluteString)|\(item.id)"
                if UserDefaults.standard.string(forKey: "backendLastReceipt") == receipt {
                    DispatchQueue.main.async { self.setBusy(false, text: "后端视频已经播放过") }
                    return
                }

                let temporaryDirectory = FileManager.default.temporaryDirectory
                    .appendingPathComponent("HoloBackend-\(UUID().uuidString)", isDirectory: true)
                try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
                defer { try? FileManager.default.removeItem(at: temporaryDirectory) }
                let incomingName = item.name.lowercased().hasSuffix(".mp4") ? item.name : "\(item.name).mp4"
                let incoming = temporaryDirectory.appendingPathComponent(incomingName)
                try item.data.write(to: incoming, options: .atomic)

                let output = self.libraryURL.appendingPathComponent(
                    "\(self.safeName(for: incoming))\(layout.fileSuffix).avi")
                self.updateStatus("后端视频转换中…", progress: 0)
                try self.convert(input: incoming, output: output, layout: layout)
                self.updateStatus("后端视频 USB 上传中…", progress: 0)
                try SerialUploader().upload(file: output) { value in
                    self.updateStatus("USB 上传：\(Int(value * 100))%", progress: value)
                }

                if let ackURL = item.ackURL {
                    let ackToken = ackURL.host == endpoint.host ? token : ""
                    try client.acknowledge(url: ackURL, id: item.id, token: ackToken)
                }
                // Persist the receipt only after the ESP32 upload and backend
                // acknowledgement both succeed. A transient ack failure will
                // therefore be retried by the next automatic poll.
                UserDefaults.standard.set(receipt, forKey: "backendLastReceipt")
                DispatchQueue.main.async {
                    self.reloadLibrary(selecting: output)
                    self.setBusy(false, text: "后端视频已接收，设备正在循环播放")
                }
            } catch {
                DispatchQueue.main.async {
                    self.setBusy(false, text: "后端接收失败：\(error.localizedDescription)")
                }
            }
        }
    }

    private func convert(input: URL, output: URL, layout: VideoLayout) throws {
        guard let ffmpeg = Bundle.main.url(forResource: "ffmpeg", withExtension: nil) else {
            throw HoloError.message("应用中缺少 FFmpeg")
        }
        try? FileManager.default.removeItem(at: output)
        let process = Process()
        process.executableURL = ffmpeg
        var arguments = ["-hide_banner", "-loglevel", "error", "-y", "-i", input.path]
        if layout == .quad {
            let filter = """
            [0:v]fps=10,scale=360:360:force_original_aspect_ratio=increase,crop=360:360,hflip,split=4[a][b][c][d];
            [a]scale=150:150[top];
            [b]scale=150:150,hflip,vflip[bottom];
            [c]scale=150:150,transpose=2[left];
            [d]scale=150:150,transpose=1[right];
            color=c=black:s=360x360:r=10[canvas];
            [canvas][top]overlay=105:0:shortest=1[q1];
            [q1][bottom]overlay=105:210:shortest=1[q2];
            [q2][left]overlay=0:105:shortest=1[q3];
            [q3][right]overlay=210:105:shortest=1[out]
            """.replacingOccurrences(of: "\n", with: "")
            arguments += ["-filter_complex", filter, "-map", "[out]"]
        } else {
            arguments += [
                "-vf", "scale=360:360:force_original_aspect_ratio=increase,crop=360:360,fps=10"
            ]
        }
        arguments += [
            "-an", "-c:v", "mjpeg", "-q:v", "7", "-pix_fmt", "yuvj420p",
            "-vtag", "MJPG", "-f", "avi", output.path
        ]
        process.arguments = arguments
        let errors = Pipe()
        process.standardOutput = FileHandle.nullDevice
        process.standardError = errors
        try process.run()
        let errorData = errors.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            let detail = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw HoloError.message(detail?.isEmpty == false ? detail! : "视频转换失败")
        }
    }

    @objc private func playSelected() {
        let row = table.selectedRow
        guard !isBusy, videos.indices.contains(row) else { return }
        let selected = videos[row]
        setBusy(true, text: "USB 上传：\(selected.lastPathComponent)")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                try SerialUploader().upload(file: selected) { value in
                    self.updateStatus("USB 上传：\(Int(value * 100))%", progress: value)
                }
                DispatchQueue.main.async { self.setBusy(false, text: "已切换，设备正在循环播放") }
            } catch {
                DispatchQueue.main.async { self.setBusy(false, text: "失败：\(error.localizedDescription)") }
            }
        }
    }

    private func updateStatus(_ text: String, progress value: Double) {
        DispatchQueue.main.async {
            self.status.stringValue = text
            self.progress.doubleValue = value
        }
    }

    private func setBusy(_ busy: Bool, text: String) {
        isBusy = busy
        status.stringValue = text
        progress.doubleValue = busy ? progress.doubleValue : 0
        table.isEnabled = !busy
        layoutPopup.isEnabled = !busy
        backendButton.isEnabled = !busy
        endpointField.isEnabled = !busy
        tokenField.isEnabled = !busy
        playButton.isEnabled = !busy && table.selectedRow >= 0
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var controller: MainController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = MainController()
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 760, height: 680),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Holo Video Uploader"
        window.minSize = NSSize(width: 700, height: 620)
        window.contentView = controller.view
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

private let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
