// agenda: reads and adds calendar events through macOS EventKit.
// Your Microsoft 365 calendar must be added in System Settings, Internet Accounts.
//
//   agenda list --days 2 [--calendars "Calendar,Work"]
//   agenda add --title "Call" --start 2026-11-04T15:00:00 --end 2026-11-04T15:30:00 [--calendar "Calendar"]

import EventKit
import Foundation

let store = EKEventStore()

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

func arg(_ name: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: name), i + 1 < a.count else { return nil }
    return a[i + 1]
}

func requestAccess() -> Bool {
    let sem = DispatchSemaphore(value: 0)
    var ok = false
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { granted, _ in ok = granted; sem.signal() }
    } else {
        store.requestAccess(to: .event) { granted, _ in ok = granted; sem.signal() }
    }
    sem.wait()
    return ok
}

let localFormat: DateFormatter = {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    f.timeZone = TimeZone.current
    return f
}()

let isoOut: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.timeZone = TimeZone.current
    return f
}()

let joinPattern = try! NSRegularExpression(
    pattern: "https://[^\\s<>\"']*(teams\\.microsoft\\.com|teams\\.live\\.com|zoom\\.us|meet\\.google\\.com|webex\\.com)[^\\s<>\"']*",
    options: [.caseInsensitive])

func joinLink(_ ev: EKEvent) -> String? {
    let haystack = [ev.url?.absoluteString, ev.location, ev.notes].compactMap { $0 }.joined(separator: " ")
    let range = NSRange(haystack.startIndex..., in: haystack)
    guard let m = joinPattern.firstMatch(in: haystack, range: range),
          let r = Range(m.range, in: haystack) else { return nil }
    return String(haystack[r])
}

func kind(_ t: EKSourceType) -> String {
    switch t {
    case .local: return "On My Mac"
    case .exchange: return "Exchange"
    case .calDAV: return "CalDAV"
    case .mobileMe: return "iCloud"
    case .subscribed: return "Subscribed"
    case .birthdays: return "Birthdays"
    @unknown default: return "Other"
    }
}

// --source outlook keeps only Microsoft 365 / Exchange calendars (added in Internet Accounts).
func calendarsInScope() -> [EKCalendar] {
    var cals = store.calendars(for: .event)
    let source = (arg("--source") ?? "all").lowercased()
    if source == "outlook" || source == "exchange" {
        let given = arg("--match") ?? ""
        let words = (given.isEmpty ? "exchange,outlook,microsoft,office 365" : given)
            .lowercased().split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        let all = cals
        cals = all.filter { cal in
            let title = cal.source.title.lowercased()
            return cal.source.sourceType == .exchange || words.contains { title.contains($0) }
        }
        if cals.isEmpty {
            var seen: [String] = []
            for c in all {
                let line = "\(c.source.title) (\(kind(c.source.sourceType)))"
                if !seen.contains(line) { seen.append(line) }
            }
            let found = seen.isEmpty ? "none" : seen.joined(separator: ", ")
            fail("No Outlook calendar found. Calendar accounts on this Mac: \(found).")
        }
    }
    return cals
}

guard CommandLine.arguments.count > 1 else { fail("usage: agenda list|add ...") }
guard requestAccess() else {
    fail("Calendar access is off. Allow Terminal in System Settings, Privacy and Security, Calendars.")
}

let command = CommandLine.arguments[1]

if command == "list" {
    let days = Int(arg("--days") ?? "2") ?? 2
    let wanted = (arg("--calendars") ?? "").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
    var cals = calendarsInScope()
    if !wanted.isEmpty { cals = cals.filter { wanted.contains($0.title) } }
    if cals.isEmpty { fail("None of the calendars named in only_calendars exist.") }

    let cal = Calendar.current
    let start = cal.startOfDay(for: Date())
    let end = cal.date(byAdding: .day, value: max(1, days), to: start)!
    let pred = store.predicateForEvents(withStart: start, end: end, calendars: cals)
    let events = store.events(matching: pred).sorted { a, b in
        if a.isAllDay != b.isAllDay { return a.isAllDay }
        return a.startDate < b.startDate
    }

    var out: [[String: Any]] = []
    for (i, ev) in events.enumerated() {
        if ev.status == .canceled { continue }
        var item: [String: Any] = [
            "id": "\(i)-\(ev.eventIdentifier ?? "x")".replacingOccurrences(of: "/", with: "_"),
            "title": ev.title ?? "(No title)",
            "start": isoOut.string(from: ev.startDate),
            "end": isoOut.string(from: ev.endDate),
            "allDay": ev.isAllDay,
            "calendar": ev.calendar.title,
            "account": ev.calendar.source.title,
            "location": (ev.location ?? "").components(separatedBy: .newlines).first ?? "",
            "eid": ev.eventIdentifier ?? "",
        ]
        if let notes = ev.notes, let r = notes.range(of: #"Deck task ([A-Za-z0-9_-]+)"#, options: .regularExpression) {
            item["deckTask"] = String(notes[r]).replacingOccurrences(of: "Deck task ", with: "")
        }
        if let link = joinLink(ev) { item["joinUrl"] = link }
        out.append(item)
    }
    let data = try! JSONSerialization.data(withJSONObject: ["events": out])
    FileHandle.standardOutput.write(data)
    exit(0)
}

if command == "add" {
    guard let title = arg("--title"),
          let s = arg("--start").flatMap({ localFormat.date(from: $0) }),
          let e = arg("--end").flatMap({ localFormat.date(from: $0) }) else {
        fail("add needs --title, --start and --end")
    }
    let ev = EKEvent(eventStore: store)
    ev.title = title
    ev.startDate = s
    ev.endDate = e
    if let notes = arg("--notes"), !notes.isEmpty { ev.notes = notes }
    let writable = calendarsInScope().filter { $0.allowsContentModifications }
    if let name = arg("--calendar"), !name.isEmpty {
        guard let target = writable.first(where: { $0.title == name }) else {
            fail("No writable calendar named \(name)")
        }
        ev.calendar = target
    } else if let d = store.defaultCalendarForNewEvents, writable.contains(where: { $0.calendarIdentifier == d.calendarIdentifier }) {
        ev.calendar = d
    } else if let main = writable.first(where: { $0.title == "Calendar" }) ?? writable.first {
        ev.calendar = main
    } else {
        fail("No calendar here accepts new events")
    }
    do {
        try store.save(ev, span: .thisEvent, commit: true)
        let out = try! JSONSerialization.data(withJSONObject: ["ok": true, "eid": ev.eventIdentifier ?? ""])
        FileHandle.standardOutput.write(out)
        exit(0)
    } catch {
        fail("Could not save: \(error.localizedDescription)")
    }
}

if command == "rename" {
    guard let eid = arg("--eid"), let title = arg("--title"),
          let ev = store.event(withIdentifier: eid) else {
        fail("rename needs --eid of an existing event and --title")
    }
    ev.title = title
    do {
        try store.save(ev, span: .thisEvent, commit: true)
        print("{\"ok\":true}")
        exit(0)
    } catch {
        fail("Could not rename: \(error.localizedDescription)")
    }
}

if command == "remove" {
    guard let eid = arg("--eid"), let ev = store.event(withIdentifier: eid) else {
        fail("remove needs --eid of an existing event")
    }
    guard (ev.notes ?? "").contains("Deck task ") else {
        fail("Deck only removes events it created")
    }
    do {
        try store.remove(ev, span: .thisEvent, commit: true)
        print("{\"ok\":true}")
        exit(0)
    } catch {
        fail("Could not remove: \(error.localizedDescription)")
    }
}

fail("unknown command \(command)")
