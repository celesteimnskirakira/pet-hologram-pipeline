import AppKit
import Darwin
import Foundation

private let supportedExtensions: Set<String> = [
    "mp4", "mov", "m4v", "avi", "mkv", "webm"
]

private enum HoloError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let text): return text
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
        try send(fd, data: Data("UPLOAD \(totalBytes)\n".utf8))
        try waitFor("[USB] ready", fd: fd, timeout: 10)

        let input = try FileHandle(forReadingFrom: file)
        defer { try? input.close() }
        var sent = 0
        while let chunk = try input.read(upToCount: 256), !chunk.isEmpty {
            try send(fd, data: chunk)
            sent += chunk.count
            progress(Double(sent) / Double(totalBytes))
            usleep(3_000)
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
    private var videos: [URL] = []
    private var isBusy = false

    private lazy var libraryURL: URL = {
        let movies = FileManager.default.urls(for: .moviesDirectory, in: .userDomainMask)[0]
        return movies.appendingPathComponent("Holo Player", isDirectory: true)
    }()

    override init() {
        super.init()
        buildInterface()
        try? FileManager.default.createDirectory(at: libraryURL, withIntermediateDirectories: true)
        reloadLibrary()
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

        progress.isIndeterminate = false
        progress.minValue = 0
        progress.maxValue = 1
        progress.doubleValue = 0
        progress.translatesAutoresizingMaskIntoConstraints = false

        status.lineBreakMode = .byTruncatingMiddle
        status.textColor = .secondaryLabelColor
        status.translatesAutoresizingMaskIntoConstraints = false

        let buttons = NSStackView(views: [importButton, playButton])
        buttons.spacing = 10
        buttons.translatesAutoresizingMaskIntoConstraints = false

        [dropView, scroll, buttons, progress, status].forEach { view.addSubview($0) }
        NSLayoutConstraint.activate([
            dropView.topAnchor.constraint(equalTo: view.topAnchor, constant: 22),
            dropView.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 22),
            dropView.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -22),
            dropView.heightAnchor.constraint(equalToConstant: 150),

            scroll.topAnchor.constraint(equalTo: dropView.bottomAnchor, constant: 16),
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
        setBusy(true, text: "准备转换…")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                var lastOutput: URL?
                for (index, input) in accepted.enumerated() {
                    let output = self.libraryURL.appendingPathComponent("\(self.safeName(for: input)).avi")
                    self.updateStatus("转换 \(index + 1)/\(accepted.count)：\(input.lastPathComponent)", progress: 0)
                    try self.convert(input: input, output: output)
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

    private func convert(input: URL, output: URL) throws {
        guard let ffmpeg = Bundle.main.url(forResource: "ffmpeg", withExtension: nil) else {
            throw HoloError.message("应用中缺少 FFmpeg")
        }
        try? FileManager.default.removeItem(at: output)
        let process = Process()
        process.executableURL = ffmpeg
        process.arguments = [
            "-hide_banner", "-loglevel", "error", "-y", "-i", input.path,
            "-vf", "scale=360:360:force_original_aspect_ratio=increase,crop=360:360,fps=10",
            "-an", "-c:v", "mjpeg", "-q:v", "5", "-pix_fmt", "yuvj420p",
            "-vtag", "MJPG", "-f", "avi", output.path
        ]
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
        playButton.isEnabled = !busy && table.selectedRow >= 0
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var controller: MainController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = MainController()
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 720, height: 560),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Holo Video Uploader"
        window.minSize = NSSize(width: 620, height: 500)
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
