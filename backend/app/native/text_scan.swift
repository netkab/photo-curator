import Foundation
import Vision
import AppKit

// One JSON request/response per line; nothing is written to disk or sent over the network.
while let line = readLine() {
    autoreleasepool {
        do {
            let obj = try JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String: String]
            guard let path = obj["path"] else { throw NSError(domain: "input", code: 1) }
            let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:])
            let detect = VNDetectTextRectanglesRequest()
            try handler.perform([detect])
            var text = ""
            let boxes = detect.results?.count ?? 0
            if boxes >= 2 {
                let request = VNRecognizeTextRequest()
                request.recognitionLevel = .accurate
                request.usesLanguageCorrection = true
                request.automaticallyDetectsLanguage = true
                let supported = try request.supportedRecognitionLanguages()
                request.recognitionLanguages = ["en-US", "he-IL"].filter { supported.contains($0) }
                try handler.perform([request])
                text = (request.results ?? []).compactMap { $0.topCandidates(1).first }.filter { $0.confidence >= 0.3 }.map { $0.string }.joined(separator: "\n")
            }
            let data = try JSONSerialization.data(withJSONObject: ["text": String(text.prefix(12000)), "boxes": boxes])
            print(String(data: data, encoding: .utf8)!)
        } catch {
            print("{\"error\":\"Local text recognition failed\"}")
        }
        fflush(stdout)
    }
}
