import Foundation

struct PlaudWebAuthCapture: Codable, Equatable {
    var authorization: String
    var xDeviceID: String
    var xPldUser: String
    var cookie: String
    var xPldTag: String?
    var baseURL: String?
    var appLanguage: String?
    var appPlatform: String?
    var editFrom: String?
    var origin: String?
    var referer: String?
    var timezone: String?

    enum CodingKeys: String, CodingKey {
        case authorization
        case xDeviceID = "x_device_id"
        case xPldUser = "x_pld_user"
        case cookie
        case xPldTag = "x_pld_tag"
        case baseURL = "base_url"
        case appLanguage = "app_language"
        case appPlatform = "app_platform"
        case editFrom = "edit_from"
        case origin
        case referer
        case timezone
    }
}
